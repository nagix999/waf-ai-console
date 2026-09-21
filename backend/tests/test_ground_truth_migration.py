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


def test_existing_items_become_draft_without_rewriting_history(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'ground-truth-migration.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=url))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "0018_dataset_source_deleted")
    engine = create_engine(url)
    spec = importlib.util.spec_from_file_location("gt_migration", root / "alembic/versions/0019_ground_truth_review.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    # 0001 creates current metadata. Strip the new fields to rehearse the real
    # pre-upgrade shape, then seed old encrypted content and frozen membership.
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert "review_status" not in {c["name"] for c in inspect(connection).get_columns("validation_dataset_items")}
        connection.execute(text("INSERT INTO validation_datasets (id,name,description,revision,created_by,created_at) "
                                "VALUES ('dataset','fixture','',2,'fixture',CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO validation_dataset_items (id,dataset_id,item_id,revision,event_ciphertext,"
            "schema_snapshot_ciphertext,encryption_key_version,input_hash,reference_verdict,source_kind,ai_visible,"
            "comment_ciphertext,internal_only,original_analysis_deleted,created_by,created_at) VALUES "
            "('item','dataset','logical',1,'encrypted-event','encrypted-schema','v1','hash','inconclusive',"
            "'reference',0,'encrypted-comment',1,0,'fixture',CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO validation_dataset_versions (id,dataset_id,revision,name,description,"
            "item_version_ids,created_by,created_at) VALUES ('version','dataset',2,'fixture','','[\"item\"]','fixture',CURRENT_TIMESTAMP)"))
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.begin() as connection:
        assert connection.execute(text("SELECT review_status,reference_verdict,event_ciphertext,schema_snapshot_ciphertext,"
            "comment_ciphertext,ai_visible,internal_only,source_label_id,source_ref,source_created_by FROM validation_dataset_items")).one() == (
            "draft", "inconclusive", "encrypted-event", "encrypted-schema", "encrypted-comment", 0, 1, None, None, None)
        assert connection.scalar(text("SELECT item_version_ids FROM validation_dataset_versions")) == '["item"]'
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    for change in ("review_status='invalid'", "review_status='approved',reference_verdict=NULL"):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text(f"UPDATE validation_dataset_items SET {change}"))
    # Reversible before reviews/imports; re-upgrade uses the same preserved data.
    command.downgrade(config, "0018_dataset_source_deleted")
    command.upgrade(config, "head")
    with engine.begin() as connection:
        connection.execute(text("UPDATE validation_dataset_items SET review_status='reviewed'"))
    with pytest.raises(RuntimeError, match="ground_truth_downgrade_requires_pre_upgrade_backup"):
        command.downgrade(config, "0018_dataset_source_deleted")
    engine.dispose()
