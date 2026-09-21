"""Rehearse R3 on disposable SQLite, including the actual pre-R3 shape."""
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


def test_upgrade_preserves_legacy_history_and_enforces_published_immutability(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'r3.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=url))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "0022_production_lifecycle")
    engine = create_engine(url)
    spec = importlib.util.spec_from_file_location("r3_migration", root / "alembic/versions/0023_ground_truth_working.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        # 0001 uses current metadata. Remove new tables/columns first, so this
        # really exercises an upgrade rather than only checkfirst branches.
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        connection.execute(text("INSERT INTO validation_datasets (id,name,description,revision,created_by,created_at) "
            "VALUES ('dataset','fixture','',1,'fixture',CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO validation_dataset_items (id,dataset_id,item_id,revision,event_ciphertext,"
            "schema_snapshot_ciphertext,encryption_key_version,input_hash,reference_verdict,source_kind,ai_visible,"
            "comment_ciphertext,internal_only,original_analysis_deleted,review_status,created_by,created_at) VALUES "
            "('item','dataset','logical',1,'encrypted-event','encrypted-schema','v1','hash','inconclusive',"
            "'reference',0,'encrypted-comment',1,0,'approved','fixture',CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO validation_dataset_versions (id,dataset_id,revision,name,description,"
            "item_version_ids,created_by,created_at) VALUES ('version','dataset',1,'fixture','','[\"item\"]','fixture',CURRENT_TIMESTAMP)"))
        columns = {name: [c["name"] for c in inspect(connection).get_columns(name)]
            for name in inspect(connection).get_table_names() if name != "alembic_version"}
        before = {name: connection.execute(text(f'SELECT {",".join(names)} FROM "{name}"')).all() for name, names in columns.items()}
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.begin() as connection:
        assert {name: connection.execute(text(f'SELECT {",".join(names)} FROM "{name}"')).all() for name, names in columns.items()} == before
        assert connection.scalar(text("SELECT is_published FROM validation_dataset_versions")) == 0
        assert connection.scalar(text("SELECT count(*) FROM validation_dataset_working_items")) == 0
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        # Only a NEW publication may carry the explicit immutable marker.
        connection.execute(text("INSERT INTO validation_dataset_versions (id,dataset_id,revision,name,description,"
            "item_version_ids,is_published,created_by,created_at) VALUES "
            "('published','dataset',2,'fixture','','[\"item\"]',1,'fixture',CURRENT_TIMESTAMP)"))
    for sql in ("UPDATE validation_dataset_versions SET name='changed' WHERE id='published'",
                "DELETE FROM validation_dataset_versions WHERE id='published'",
                "UPDATE validation_dataset_items SET reference_verdict='false_positive' WHERE id='item'",
                "DELETE FROM validation_dataset_items WHERE id='item'"):
        with pytest.raises(IntegrityError, match="immutable_published"), engine.begin() as connection:
            connection.execute(text(sql))
    with engine.begin() as connection:
        connection.execute(text("UPDATE validation_dataset_items SET original_analysis_id=NULL,original_analysis_deleted=1 WHERE id='item'"))
        assert connection.scalar(text("SELECT event_ciphertext FROM validation_dataset_items WHERE id='item'")) == "encrypted-event"
    with pytest.raises(RuntimeError, match="ground_truth_history_requires_pre_upgrade_backup"):
        command.downgrade(config, "0022_production_lifecycle")
    engine.dispose()
