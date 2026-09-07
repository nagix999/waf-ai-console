"""Offline temporary SQLite checks; never touch the configured service DB."""
import importlib.util
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.database import Base, build_engine
from test_provider_migrations import seed_history, snapshot_history


REVISION = "0009_model_validation_dataset"


def migration_module():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / f"{REVISION}.py"
    spec = importlib.util.spec_from_file_location("model_validation_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def existing_columns(snapshot):
    for row in snapshot["analyses"]:
        row.pop("model_test_run_id", None)
    for row in snapshot["vllm_test_runs"]:
        for name in ("include_dataset", "dataset_version", "dataset_hash"):
            row.pop(name, None)
    return snapshot


def seed_legacy_history(connection):
    # Build synthetic fixtures with current ORM defaults in a separate, empty
    # memory DB, then insert only historical columns into the downgraded schema.
    fixture_engine = build_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(fixture_engine)
    try:
        with fixture_engine.begin() as fixture:
            seed_history(fixture)
            rows = existing_columns(snapshot_history(fixture))
    finally:
        fixture_engine.dispose()
    for table, records in rows.items():
        for record in records:
            columns = ", ".join(f'"{name}"' for name in record)
            values = ", ".join(f":{name}" for name in record)
            connection.execute(text(f'INSERT INTO "{table}" ({columns}) VALUES ({values})'), record)


def test_dataset_migration_preserves_ciphertexts_results_and_legacy_test_history(tmp_path):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'dataset-legacy.db'}")
    Base.metadata.create_all(engine)
    migration = migration_module()
    try:
        with engine.begin() as connection:
            tables = set(inspect(connection).get_table_names())
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            seed_legacy_history(connection)
            before = snapshot_history(connection)
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()
            after = snapshot_history(connection)
            assert after["analyses"][0]["model_test_run_id"] is None
            assert after["vllm_test_runs"][0]["include_dataset"] == 0
            assert after["vllm_test_runs"][0]["dataset_hash"] is None
            assert after["vllm_test_runs"][0]["dataset_version"] is None
            assert existing_columns(after) == before
            assert set(inspect(connection).get_table_names()) == tables
            assert connection.scalar(text("SELECT COUNT(*) FROM analysis_labels")) == 0
            assert connection.scalar(text("SELECT COUNT(*) FROM analyses")) == 1
            assert connection.scalar(text("SELECT COUNT(*) FROM vllm_test_runs")) == 1
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
            assert "ix_analyses_model_test_run_id" in {index["name"] for index in inspect(connection).get_indexes("analyses")}
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError, match="^cannot_downgrade_model_validation_with_analysis_history$"):
                    migration.downgrade()
            assert existing_columns(snapshot_history(connection)) == before
    finally:
        engine.dispose()


def test_dataset_foreign_key_rejects_missing_run_and_prevents_history_deletion(tmp_path):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'dataset-fk.db'}")
    Base.metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration_module().downgrade()
            seed_legacy_history(connection)
            with Operations.context(MigrationContext.configure(connection)):
                migration_module().upgrade()
            with pytest.raises(IntegrityError):
                connection.execute(text("UPDATE analyses SET model_test_run_id = 'synthetic-missing-run' WHERE id = 'legacy-analysis'"))
            connection.execute(text("UPDATE analyses SET model_test_run_id = 'legacy-test' WHERE id = 'legacy-analysis'"))
            with pytest.raises(IntegrityError):
                connection.execute(text("DELETE FROM vllm_test_runs WHERE id = 'legacy-test'"))
            assert connection.scalar(text("SELECT COUNT(*) FROM vllm_test_runs WHERE id = 'legacy-test'")) == 1
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    finally:
        engine.dispose()


@pytest.mark.parametrize("history_kind", ["test_run", "linked_analysis"])
def test_dataset_downgrade_refuses_either_form_of_saved_history(tmp_path, history_kind):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'dataset-protected.db'}")
    Base.metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            seed_history(connection)
            if history_kind == "test_run":
                connection.execute(text("UPDATE vllm_test_runs SET include_dataset = 1, dataset_version = 'synthetic-v1', dataset_hash = 'synthetic-hash' WHERE id = 'legacy-test'"))
            else:
                connection.execute(text("UPDATE analyses SET model_test_run_id = 'legacy-test' WHERE id = 'legacy-analysis'"))
            before = snapshot_history(connection)
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError, match="^cannot_downgrade_with_model_validation_history$"):
                    migration_module().downgrade()
            assert snapshot_history(connection) == before
            assert "model_test_run_id" in {column["name"] for column in inspect(connection).get_columns("analyses")}
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    finally:
        engine.dispose()


def test_fresh_head_does_not_enqueue_or_seed_and_empty_downgrade_is_reversible(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[1]
    url = f"sqlite+pysqlite:///{tmp_path / 'dataset-fresh.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=url))
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(config, REVISION)
    engine = build_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == REVISION
            for table in ("analyses", "analysis_labels", "vllm_profiles", "vllm_test_runs", "service_api_keys", "internal_egress_targets"):
                assert connection.scalar(text(f"SELECT COUNT(*) FROM {table}")) == 0
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        command.downgrade(config, "0008_service_api_keys")
        with engine.connect() as connection:
            assert "model_test_run_id" not in {column["name"] for column in inspect(connection).get_columns("analyses")}
            assert not {"include_dataset", "dataset_version", "dataset_hash"} & {column["name"] for column in inspect(connection).get_columns("vllm_test_runs")}
        command.upgrade(config, REVISION)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == REVISION
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    finally:
        engine.dispose()
