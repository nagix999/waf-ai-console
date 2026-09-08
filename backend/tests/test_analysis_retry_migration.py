"""Rebuild the old UNIQUE only on an offline connection; preserve every row."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0013_analysis_retries_keys.py"
    spec = importlib.util.spec_from_file_location("analysis_retry_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_legacy(connection):
    connection.execute(text("CREATE TABLE service_api_keys (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, key_hash TEXT NOT NULL)"))
    connection.execute(text("INSERT INTO service_api_keys VALUES ('key','synthetic','synthetic-not-a-key')"))
    connection.execute(text("CREATE TABLE analyses (id TEXT PRIMARY KEY, source_system TEXT NOT NULL, event_id TEXT NOT NULL, payload_ciphertext TEXT NOT NULL, CONSTRAINT uq_analysis_source_event UNIQUE (source_system,event_id))"))
    connection.execute(text("INSERT INTO analyses VALUES ('original','synthetic','event','synthetic-ciphertext')"))
    for table, behavior in (("agent_runs", "CASCADE"), ("analysis_labels", "CASCADE"), ("test_run_items", "RESTRICT"), ("reviews", "CASCADE")):
        connection.execute(text(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, analysis_id TEXT REFERENCES analyses(id) ON DELETE {behavior}, history TEXT)"))
        connection.execute(text(f"INSERT INTO {table} VALUES ('history','original','synthetic-immutable')"))


def test_offline_upgrade_preserves_ciphertexts_keys_and_all_child_histories(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'synthetic-retry.db'}")
    with engine.begin() as connection:
        create_legacy(connection)
        names = inspect(connection).get_table_names()
        columns = {name: [column["name"] for column in inspect(connection).get_columns(name)] for name in names}
        before = {name: connection.execute(text(f"SELECT * FROM {name}")).all() for name in names}
        assert connection.scalar(text("PRAGMA foreign_keys")) == 0
        with Operations.context(MigrationContext.configure(connection)):
            migration().upgrade()
            migration().upgrade()
        for name in names:
            assert connection.execute(text(f"SELECT {','.join(columns[name])} FROM {name}")).all() == before[name]
        assert connection.execute(text("SELECT service_api_key_id,retry_of_analysis_id,execution_snapshot_ciphertext FROM analyses")).one() == (None, None, None)
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    with engine.connect() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.commit()
        with pytest.raises(IntegrityError):
            connection.execute(text("INSERT INTO analyses (id,source_system,event_id,payload_ciphertext) VALUES ('duplicate','synthetic','event','other')"))
        connection.rollback()
        connection.execute(text("INSERT INTO analyses (id,source_system,event_id,payload_ciphertext,retry_of_analysis_id,retry_idempotency_key,service_api_key_id) VALUES ('retry','synthetic','event','synthetic-ciphertext','original','unique-retry','key')"))
        connection.commit()
        with pytest.raises(IntegrityError):
            connection.execute(text("INSERT INTO analyses (id,source_system,event_id,payload_ciphertext,retry_of_analysis_id) VALUES ('second-retry','synthetic','event','other','original')"))
        connection.rollback()
        with pytest.raises(IntegrityError):
            connection.execute(text("DELETE FROM service_api_keys WHERE id='key'"))
        connection.rollback()
        with pytest.raises(IntegrityError):
            connection.execute(text("DELETE FROM analyses WHERE id='original'"))
        connection.rollback()
        assert connection.scalar(text("SELECT COUNT(*) FROM analysis_labels")) == 1
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="pre_upgrade_backup"):
                migration().downgrade()
    engine.dispose()


def test_live_foreign_key_connection_is_rejected_before_any_changes(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'synthetic-live-guard.db'}")
    with engine.begin() as connection:
        create_legacy(connection)
    with engine.connect() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.commit()
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="offline_foreign_keys_disabled"):
                migration().upgrade()
        assert "deleted_at" not in {column["name"] for column in inspect(connection).get_columns("service_api_keys")}
        assert connection.scalar(text("SELECT COUNT(*) FROM agent_runs")) == 1
    engine.dispose()
