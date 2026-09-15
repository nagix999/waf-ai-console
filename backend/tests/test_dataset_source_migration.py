import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_existing_0017_items_default_to_live_source_and_downgrade_protects_deleted_source(tmp_path):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0018_dataset_source_deleted.py"
    spec = importlib.util.spec_from_file_location("source_migration", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'source.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE validation_dataset_items (id TEXT PRIMARY KEY, event_ciphertext TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO validation_dataset_items VALUES ('fixture','unchanged-ciphertext')"))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade(); module.upgrade()
            assert connection.execute(text("SELECT * FROM validation_dataset_items")).one() == ("fixture", "unchanged-ciphertext", 0)
            module.downgrade()
            assert "original_analysis_deleted" not in {c["name"] for c in inspect(connection).get_columns("validation_dataset_items")}
            module.upgrade()
            connection.execute(text("UPDATE validation_dataset_items SET original_analysis_deleted=1"))
            with pytest.raises(RuntimeError, match="requires_pre_upgrade_backup"):
                module.downgrade()
    engine.dispose()
