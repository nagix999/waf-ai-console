"""Additive migration rehearsal on temporary databases, never service data."""
import importlib.util
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

from app.config import Settings
from app.database import build_engine


def module():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0010_test_runs.py"
    spec = importlib.util.spec_from_file_location("named_test_migration", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_fresh_upgrade_is_empty_and_repeatable_and_downgrade_preserves_parents(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[1]
    url = f"sqlite+pysqlite:///{tmp_path / 'named-tests.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=url))
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(config, "head")
    engine = build_engine(url)
    try:
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0013_analysis_retries_keys"
            for table in ("test_runs", "test_run_items", "analyses", "analysis_labels", "vllm_test_runs"):
                assert connection.scalar(text(f"SELECT COUNT(*) FROM {table}")) == 0
            with Operations.context(MigrationContext.configure(connection)):
                module().upgrade()
                module().upgrade()
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        command.downgrade(config, "0009_model_validation_dataset")
        with engine.connect() as connection:
            assert "test_runs" not in inspect(connection).get_table_names()
            assert "analyses" in inspect(connection).get_table_names()
            # Nullable columns deliberately survive SQLite downgrade: no
            # referenced parent rebuild, no cascading history deletion.
            assert {"name", "idempotency_key", "request_hash"} <= {
                column["name"] for column in inspect(connection).get_columns("vllm_test_runs")}
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    finally:
        engine.dispose()


def test_downgrade_refuses_named_history(client, event_payload):
    client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"})
    response = client.post("/api/v1/test-runs", json={"name": "합성 이력 보존",
        "idempotency_key": "migration-synthetic", "event": event_payload})
    assert response.status_code == 202
    with client.app.state.engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="cannot_downgrade_with_named_test_history"):
                module().downgrade()
        assert connection.scalar(text("SELECT COUNT(*) FROM analyses")) == 1
        assert connection.scalar(text("SELECT COUNT(*) FROM test_runs")) == 1
