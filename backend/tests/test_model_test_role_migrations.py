"""0011 rehearsal: add one role, preserve all existing profile/history values."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.database import build_engine


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0011_model_test_role.py"
    spec = importlib.util.spec_from_file_location("model_test_role_migration", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_role_migration_does_not_auto_assign_or_rebuild_existing_parent(tmp_path):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'synthetic-role-migration.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE vllm_profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL, api_key_ciphertext TEXT)"))
            connection.execute(text("CREATE UNIQUE INDEX uq_vllm_single_production ON vllm_profiles(status) WHERE status = 'production'"))
            connection.execute(text("CREATE TABLE synthetic_history (id TEXT PRIMARY KEY, profile_id TEXT REFERENCES vllm_profiles(id) ON DELETE CASCADE, ciphertext TEXT)"))
            connection.execute(text("INSERT INTO vllm_profiles VALUES ('one', 'Synthetic One', 'production', 'synthetic-encrypted-key'), ('two', 'Synthetic Two', 'verified', NULL)"))
            connection.execute(text("INSERT INTO synthetic_history VALUES ('history', 'one', 'synthetic-encrypted-history')"))
            before = connection.execute(text("SELECT id,name,status,api_key_ciphertext FROM vllm_profiles ORDER BY id")).all()
            with Operations.context(MigrationContext.configure(connection)):
                migration().upgrade()
                migration().upgrade()
            assert connection.execute(text("SELECT id,name,status,api_key_ciphertext FROM vllm_profiles ORDER BY id")).all() == before
            assert connection.scalar(text("SELECT COUNT(*) FROM vllm_profiles WHERE is_test = 1")) == 0
            assert connection.scalar(text("SELECT COUNT(*) FROM synthetic_history")) == 1
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
            connection.execute(text("UPDATE vllm_profiles SET is_test = 1 WHERE id = 'one'"))
            with pytest.raises(IntegrityError):
                connection.execute(text("UPDATE vllm_profiles SET is_test = 1 WHERE id = 'two'"))
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError, match="cannot_downgrade_with_assigned_test_profile"):
                    migration().downgrade()
            connection.execute(text("UPDATE vllm_profiles SET is_test = 0 WHERE id = 'one'"))
            with Operations.context(MigrationContext.configure(connection)):
                migration().downgrade()
            # SQLite retains an unused false column to avoid a parent rebuild.
            assert "is_test" in {column["name"] for column in inspect(connection).get_columns("vllm_profiles")}
            assert connection.scalar(text("SELECT ciphertext FROM synthetic_history")) == "synthetic-encrypted-history"
            with Operations.context(MigrationContext.configure(connection)):
                migration().upgrade()
            assert connection.execute(text("SELECT id,name,status,api_key_ciphertext FROM vllm_profiles ORDER BY id")).all() == before
            assert "uq_vllm_single_test" in {index["name"] for index in inspect(connection).get_indexes("vllm_profiles")}
    finally:
        engine.dispose()
