"""0012 is additive; synthetic cascading histories survive upgrade/downgrade."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.database import build_engine


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0012_input_schemas.py"
    spec = importlib.util.spec_from_file_location("input_schema_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_input_schema_migration_preserves_all_existing_rows_and_cascades(tmp_path):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'synthetic-input-schema.db'}")
    try:
        with engine.begin() as connection:
            for table in ("analyses", "test_runs", "vllm_test_runs"):
                connection.execute(text(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, ciphertext TEXT NOT NULL)"))
                connection.execute(text(f"INSERT INTO {table} VALUES ('legacy', 'synthetic-encrypted-history')"))
                connection.execute(text(f"CREATE TABLE {table}_audit (id TEXT PRIMARY KEY, parent_id TEXT REFERENCES {table}(id) ON DELETE CASCADE)"))
                connection.execute(text(f"INSERT INTO {table}_audit VALUES ('audit', 'legacy')"))
            before = {table: connection.execute(text(f"SELECT * FROM {table}")).all() for table in inspect(connection).get_table_names()}
            with Operations.context(MigrationContext.configure(connection)):
                migration().upgrade()
                migration().upgrade()
            for table, rows in before.items():
                selected = "id, ciphertext" if table in migration().TARGETS else "*"
                assert connection.execute(text(f"SELECT {selected} FROM {table}")).all() == rows
            for table in migration().TARGETS:
                assert connection.execute(text(f"SELECT input_schema_version_id,input_schema_snapshot_ciphertext FROM {table}")).one() == (None, None)
                foreign_keys = connection.execute(text(f"PRAGMA foreign_key_list({table})")).mappings().all()
                assert any(row["from"] == "input_schema_version_id" and row["on_delete"] == "RESTRICT" for row in foreign_keys)
            assert connection.scalar(text("SELECT COUNT(*) FROM input_schema_versions")) == 0
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
            # Unused nullable additions are retained on SQLite for a safe downgrade.
            with Operations.context(MigrationContext.configure(connection)):
                migration().downgrade()
                migration().upgrade()
            assert connection.scalar(text("SELECT COUNT(*) FROM analyses_audit")) == 1
            connection.execute(text("INSERT INTO input_schema_versions VALUES ('schema',1,'Synthetic','Synthetic note',NULL,'hash','ciphertext','v1','synthetic','2026-09-07')"))
            connection.execute(text("UPDATE analyses SET input_schema_version_id='schema',input_schema_snapshot_ciphertext='synthetic-snapshot'"))
            with pytest.raises(IntegrityError):
                connection.execute(text("DELETE FROM input_schema_versions WHERE id='schema'"))
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError, match="cannot_downgrade_with_input_schema_history"):
                    migration().downgrade()
            assert connection.scalar(text("SELECT input_schema_snapshot_ciphertext FROM analyses")) == "synthetic-snapshot"
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    finally:
        engine.dispose()
