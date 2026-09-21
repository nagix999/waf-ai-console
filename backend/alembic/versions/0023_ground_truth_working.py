"""R3 Working Draft and explicitly published Ground Truth revisions.

Historical versions remain legacy. Draft copies are materialized at the first
write under the existing SQLite write lock; no existing data is rewritten.
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_ground_truth_working"
down_revision = "0022_production_lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("validation_dataset_versions")}
    for column in (
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("membership_hash", sa.String(64)), sa.Column("publish_metadata", sa.JSON()),
    ):
        if column.name not in existing:
            op.add_column("validation_dataset_versions", column)
    from app.models import ValidationDatasetWorkingState, ValidationDatasetWorkingItem
    ValidationDatasetWorkingState.__table__.create(bind, checkfirst=True)
    ValidationDatasetWorkingItem.__table__.create(bind, checkfirst=True)
    if bind.dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(f"CREATE TRIGGER IF NOT EXISTS published_dataset_no_{action.lower()} BEFORE {action} "
                "ON validation_dataset_versions WHEN OLD.is_published = 1 "
                "BEGIN SELECT RAISE(ABORT, 'immutable_published_revision'); END")
        # Source detachment for an explicitly deleted analysis remains allowed;
        # copied input, label, membership and provenance are never editable.
        columns = [c["name"] for c in sa.inspect(bind).get_columns("validation_dataset_items")
            if c["name"] not in {"original_analysis_id", "original_analysis_deleted"}]
        changed = " OR ".join(f"NEW.{name} IS NOT OLD.{name}" for name in columns)
        member = "EXISTS (SELECT 1 FROM validation_dataset_versions v, json_each(v.item_version_ids) m WHERE v.is_published = 1 AND m.value = OLD.id)"
        op.execute(f"CREATE TRIGGER IF NOT EXISTS published_item_no_update BEFORE UPDATE ON validation_dataset_items "
            f"WHEN ({member}) AND ({changed}) BEGIN SELECT RAISE(ABORT, 'immutable_published_item'); END")
        op.execute(f"CREATE TRIGGER IF NOT EXISTS published_item_no_delete BEFORE DELETE ON validation_dataset_items "
            f"WHEN {member} BEGIN SELECT RAISE(ABORT, 'immutable_published_item'); END")


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT count(*) FROM validation_dataset_working_states")) or bind.scalar(
        sa.text("SELECT count(*) FROM validation_dataset_versions WHERE is_published = 1")
    ):
        raise RuntimeError("ground_truth_history_requires_pre_upgrade_backup")
    op.drop_table("validation_dataset_working_items")
    op.drop_table("validation_dataset_working_states")
    if bind.dialect.name == "sqlite":
        for trigger in ("published_dataset_no_update", "published_dataset_no_delete", "published_item_no_update", "published_item_no_delete"):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for column in ("publish_metadata", "membership_hash", "is_published"):
        op.drop_column("validation_dataset_versions", column)
