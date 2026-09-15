"""Preserve dataset copies when an explicitly selected source is deleted."""
from alembic import op
import sqlalchemy as sa

revision = "0018_dataset_source_deleted"
down_revision = "0017_validation_data"
branch_labels = None
depends_on = None


def upgrade():
    if "original_analysis_deleted" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("validation_dataset_items")}:
        op.add_column("validation_dataset_items", sa.Column("original_analysis_deleted", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM validation_dataset_items WHERE original_analysis_deleted")):
        raise RuntimeError("dataset_source_downgrade_requires_pre_upgrade_backup")
    op.drop_column("validation_dataset_items", "original_analysis_deleted")
