"""Run real migration on synthetic fresh and previous-version databases."""
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
from app.database import build_engine

HEAD = "0032_signature_explanations"
TABLES = ("analyses", "test_runs", "vllm_test_runs", "test_configuration_defaults")


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0032_signature_explanations.py"
    spec = importlib.util.spec_from_file_location("signature_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_preserves_all_existing_parents_cascades_and_no_backfill(tmp_path):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'synthetic-existing.db'}")
    with engine.begin() as connection:
        for table in TABLES:
            connection.execute(text(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, ciphertext TEXT NOT NULL)"))
            connection.execute(text(f"INSERT INTO {table} VALUES ('legacy','synthetic-history')"))
            connection.execute(text(f"CREATE TABLE {table}_audit (id TEXT PRIMARY KEY, parent_id TEXT REFERENCES {table}(id) ON DELETE CASCADE)"))
            connection.execute(text(f"INSERT INTO {table}_audit VALUES ('audit','legacy')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration().upgrade()
            migration().upgrade()
        for table in TABLES:
            assert connection.execute(text(f"SELECT id,ciphertext FROM {table}")).all() == [("legacy", "synthetic-history")]
            assert connection.scalar(text(f"SELECT count(*) FROM {table}_audit")) == 1
            assert connection.scalar(text(f"SELECT signature_explanation_version_id FROM {table}")) is None
            assert any(foreign_key["from"] == "signature_explanation_version_id" and foreign_key["on_delete"] == "RESTRICT"
                for foreign_key in connection.execute(text(f"PRAGMA foreign_key_list({table})")).mappings())
        assert connection.scalar(text("SELECT active_version_id FROM signature_explanation_configuration")) is None
        assert connection.scalar(text("SELECT count(*) FROM signature_explanation_versions")) == 0
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        with Operations.context(MigrationContext.configure(connection)):
            migration().downgrade()
            migration().upgrade()
        connection.execute(text("UPDATE analyses SET signature_explanation_snapshot_ciphertext='synthetic-encrypted-empty-snapshot'"))
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="snapshots_require_current_schema"):
                migration().downgrade()
        connection.execute(text("UPDATE analyses SET signature_explanation_snapshot_ciphertext=NULL"))
        connection.execute(text("INSERT INTO signature_explanation_versions "
            "(id,version_no,name,change_reason,fingerprint,entry_count,definition_ciphertext,encryption_key_version,created_by,created_at) "
            "VALUES ('fixture-version',1,'Synthetic','Synthetic','fixture-hash',0,'encrypted','test','fixture','2026-11-17')"))
        connection.execute(text("UPDATE analyses SET signature_explanation_version_id='fixture-version'"))
        with pytest.raises(IntegrityError):
            connection.execute(text("DELETE FROM signature_explanation_versions WHERE id='fixture-version'"))
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="history_requires_current_schema"):
                migration().downgrade()
        assert connection.scalar(text("SELECT count(*) FROM analyses_audit")) == 1
    engine.dispose()


@pytest.mark.parametrize("upgrade_existing", [False, True])
def test_actual_fresh_and_existing_alembic_upgrade(tmp_path, monkeypatch, upgrade_existing):
    import app.config
    settings = Settings(database_url=f"sqlite+pysqlite:///{tmp_path / 'synthetic.db'}", admin_username="admin",
        admin_password="test-password", session_secret="synthetic-session-secret",
        data_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    monkeypatch.setattr(app.config, "get_settings", lambda: settings)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    if upgrade_existing:
        command.upgrade(config, "0031_parser_rule_workbench")
        engine = build_engine(settings.database_url)
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO worker_heartbeats (worker_id,worker_kind,observed_at) "
                "VALUES ('synthetic-existing','analysis','2026-11-17')"))
        engine.dispose()
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    engine = build_engine(settings.database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
        assert {"signature_explanation_versions", "signature_explanation_configuration"} <= set(inspect(connection).get_table_names())
        assert connection.scalar(text("SELECT active_version_id FROM signature_explanation_configuration")) is None
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        if upgrade_existing:
            assert connection.scalar(text("SELECT count(*) FROM worker_heartbeats WHERE worker_id='synthetic-existing'")) == 1
    engine.dispose()
