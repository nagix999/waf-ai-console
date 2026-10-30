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


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0029_parser_management.py"
    spec = importlib.util.spec_from_file_location("parser_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parser_upgrade_preserves_existing_parent_rows_and_cascading_histories(tmp_path):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'synthetic-previous.db'}")
    tables = ("analyses", "test_runs", "vllm_test_runs", "test_configuration_defaults")
    with engine.begin() as connection:
        for table in tables:
            connection.execute(text(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, ciphertext TEXT NOT NULL)"))
            connection.execute(text(f"INSERT INTO {table} VALUES ('legacy','synthetic-history')"))
            connection.execute(text(f"CREATE TABLE {table}_audit (id TEXT PRIMARY KEY, parent_id TEXT REFERENCES {table}(id) ON DELETE CASCADE)"))
            connection.execute(text(f"INSERT INTO {table}_audit VALUES ('audit','legacy')"))
        connection.execute(text("CREATE TABLE vllm_profiles (id TEXT PRIMARY KEY)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration().upgrade()
            migration().upgrade()
        for table in tables:
            assert connection.execute(text(f"SELECT id,ciphertext FROM {table}")).all() == [("legacy", "synthetic-history")]
            assert connection.scalar(text(f"SELECT count(*) FROM {table}_audit")) == 1
            assert connection.scalar(text(f"SELECT parser_version_id FROM {table}")) is None
            assert any(fk["from"] == "parser_version_id" and fk["on_delete"] == "RESTRICT" for fk in
                       connection.execute(text(f"PRAGMA foreign_key_list({table})")).mappings())
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        with Operations.context(MigrationContext.configure(connection)):
            migration().downgrade()
            migration().upgrade()
        connection.execute(text("INSERT INTO parser_versions (id,name,engine_version,options_json,content_hash,created_by,created_at) "
            "VALUES ('generic-http-v3','Baseline','generic-http-v3','{}','fixture-hash','system','2026-10-28')"))
        connection.execute(text("UPDATE analyses SET parser_version_id='generic-http-v3',parser_snapshot_json='{}'"))
        with pytest.raises(IntegrityError):
            connection.execute(text("DELETE FROM parser_versions WHERE id='generic-http-v3'"))
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="parser_snapshots_require_current_schema"):
                migration().downgrade()
        assert connection.scalar(text("SELECT count(*) FROM analyses_audit")) == 1
    engine.dispose()


def test_actual_fresh_alembic_head_and_repeat_upgrade(tmp_path, monkeypatch):
    import app.config
    settings = Settings(database_url=f"sqlite+pysqlite:///{tmp_path / 'fresh.db'}",
        admin_username="admin", admin_password="test-password", session_secret="synthetic-session-secret",
        data_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    monkeypatch.setattr(app.config, "get_settings", lambda: settings)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    engine = build_engine(settings.database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0029_parser_management"
        assert {"parser_versions", "parser_configuration", "parser_jobs"} <= set(inspect(connection).get_table_names())
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()
