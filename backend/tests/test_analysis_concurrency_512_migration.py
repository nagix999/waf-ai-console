"""Temporary SQLite migration coverage; no live DB, services or model calls."""
import importlib.util
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.database import Base
from app.models import Analysis, ConcurrencyConfiguration


HEAD = "0034_analysis_concurrency_512"
PREVIOUS = "0033_feedback_provenance"
RAW_LIMITS = '{ "10.0.0.10:8000": 7, "10.0.0.20:8000": 3 }'


def migration_config(tmp_path, monkeypatch, filename):
    backend = Path(__file__).resolve().parents[1]
    url = f"sqlite+pysqlite:///{tmp_path / filename}"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=url))
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    return config, create_engine(url)


def saved_rows(engine):
    with engine.connect() as connection:
        return connection.execute(text(
            "SELECT id, revision, production, test, server_limits FROM concurrency_configuration ORDER BY id"
        )).all()


def table_definition(engine, name):
    with engine.connect() as connection:
        return connection.scalar(text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"), {"name": name})


def preserved_tables(engine):
    with engine.connect() as connection:
        schema = connection.execute(text(
            "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE tbl_name IN ('analyses', 'llm_call_slots') ORDER BY name"
        )).all()
        analyses = connection.execute(text("SELECT id, payload_ciphertext, result_json FROM analyses ORDER BY id")).all()
        slots = connection.execute(text("SELECT id, server_key, expires_at FROM llm_call_slots ORDER BY id")).all()
        return schema, analyses, slots


def seed_existing(engine, *, limit=32, occupied=True):
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE concurrency_configuration"))
        if limit == 32:
            # Execute the actual immutable 0015 schema instead of approximating
            # its CHECK formatting or accidentally using current metadata.
            path = Path(__file__).resolve().parents[1] / "alembic/versions/0015_concurrency.py"
            spec = importlib.util.spec_from_file_location("original_concurrency_migration", path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
        else:
            ConcurrencyConfiguration.__table__.create(connection)
        if occupied:
            connection.execute(text("""INSERT INTO concurrency_configuration
                (id, revision, production, test, server_limits) VALUES (1, 19, 32, 17, :limits)"""), {"limits": RAW_LIMITS})
        connection.execute(Analysis.__table__.insert().values(
            id="synthetic-analysis", source_system="synthetic-source", event_id="synthetic-event",
            company_name="Synthetic", src_ip="192.0.2.1", dest_ip="198.51.100.1", waf_vendor="generic",
            waf_action="D", payload_ciphertext="synthetic-encrypted-placeholder", encryption_key_version="synthetic-v1",
            result_json={"synthetic": "unchanged"},
        ))
        connection.execute(text("""INSERT INTO llm_call_slots(id, server_key, expires_at)
            VALUES ('synthetic-slot', 'synthetic-server', '2099-01-01 00:00:00.000000')"""))


def assert_range(engine, accepted, rejected):
    with engine.begin() as connection:
        if not connection.scalar(text("SELECT COUNT(*) FROM concurrency_configuration")):
            connection.execute(text("""INSERT INTO concurrency_configuration
                (id, revision, production, test, server_limits) VALUES (1, 0, 1, 1, '{}')"""))
        connection.execute(text("UPDATE concurrency_configuration SET production=:limit, test=:limit"), {"limit": accepted})
    for column, bad_value in (("production", rejected), ("test", rejected), ("production", 0), ("test", 0)):
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text(f"UPDATE concurrency_configuration SET {column}=:limit"), {"limit": bad_value})
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text("""INSERT INTO concurrency_configuration
                (id, revision, production, test, server_limits) VALUES (2, 0, 1, 1, '{}')"""))


def test_fresh_full_upgrade_matches_current_metadata_and_keeps_default_unseeded(tmp_path, monkeypatch):
    config, engine = migration_config(tmp_path, monkeypatch, "fresh.db")
    try:
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
            assert {check["name"] for check in inspect(connection).get_check_constraints("concurrency_configuration")} == {
                "ck_concurrency_singleton", "ck_concurrency_range"}
        assert saved_rows(engine) == []
        definition = table_definition(engine, "concurrency_configuration")
        command.upgrade(config, "head")
        assert table_definition(engine, "concurrency_configuration") == definition
        assert_range(engine, 512, 513)
    finally:
        engine.dispose()


@pytest.mark.parametrize("limit", [32, 512], ids=["actual-old-check", "current-metadata-check"])
@pytest.mark.parametrize("occupied", [False, True], ids=["empty", "configured"])
def test_existing_upgrade_preserves_exact_config_json_and_all_other_tables(tmp_path, monkeypatch, limit, occupied):
    config, engine = migration_config(tmp_path, monkeypatch, "existing.db")
    try:
        seed_existing(engine, limit=limit, occupied=occupied)
        command.stamp(config, PREVIOUS)
        before_rows, before_others = saved_rows(engine), preserved_tables(engine)
        before_definition = table_definition(engine, "concurrency_configuration")
        command.upgrade(config, "head")
        assert saved_rows(engine) == before_rows
        assert preserved_tables(engine) == before_others
        definition = table_definition(engine, "concurrency_configuration")
        if limit == 512:
            assert definition == before_definition
        command.upgrade(config, "head")
        assert table_definition(engine, "concurrency_configuration") == definition
        assert saved_rows(engine) == before_rows
        assert_range(engine, 512, 513)
    finally:
        engine.dispose()


@pytest.mark.parametrize("production,test", [(33, 1), (1, 512), (512, 512)])
def test_downgrade_refuses_above_32_before_any_schema_or_value_change(tmp_path, monkeypatch, production, test):
    config, engine = migration_config(tmp_path, monkeypatch, "blocked.db")
    try:
        seed_existing(engine, limit=512)
        command.stamp(config, HEAD)
        with engine.begin() as connection:
            connection.execute(text("UPDATE concurrency_configuration SET production=:production, test=:test"),
                               {"production": production, "test": test})
        before_rows, before_others = saved_rows(engine), preserved_tables(engine)
        before_definition = table_definition(engine, "concurrency_configuration")
        with pytest.raises(RuntimeError, match="^concurrency_limit_downgrade_requires_32_or_less$"):
            command.downgrade(config, PREVIOUS)
        assert saved_rows(engine) == before_rows
        assert preserved_tables(engine) == before_others
        assert table_definition(engine, "concurrency_configuration") == before_definition
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
    finally:
        engine.dispose()


@pytest.mark.parametrize("occupied", [False, True], ids=["empty", "configured"])
def test_allowed_downgrade_restores_32_and_preserves_saved_limits(tmp_path, monkeypatch, occupied):
    config, engine = migration_config(tmp_path, monkeypatch, "rollback.db")
    try:
        seed_existing(engine, limit=512, occupied=occupied)
        command.stamp(config, HEAD)
        before_rows, before_others = saved_rows(engine), preserved_tables(engine)
        command.downgrade(config, PREVIOUS)
        assert saved_rows(engine) == before_rows
        assert preserved_tables(engine) == before_others
        assert_range(engine, 32, 33)
        command.upgrade(config, "head")
        assert_range(engine, 512, 513)
    finally:
        engine.dispose()
