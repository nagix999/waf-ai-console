"""Immutable Ground Truth review states; old reference data remains Draft."""
from alembic import op
import sqlalchemy as sa

revision = "0019_ground_truth_review"
down_revision = "0018_dataset_source_deleted"
branch_labels = None
depends_on = None

TABLE = "validation_dataset_items"
CHECKS = {
    "ck_dataset_review_status": "review_status IN ('draft', 'reviewed', 'approved')",
    "ck_dataset_review_verdict": "review_status = 'draft' OR reference_verdict IS NOT NULL",
}


def upgrade():
    # 0001 creates current metadata on a fresh install, so both paths must work.
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    definitions = (
        sa.Column("review_status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("source_ref", sa.String(120)),
        sa.Column("source_label_id", sa.String(36)),
        sa.Column("source_created_by", sa.String(255)),
    )
    for column in definitions:
        if column.name not in columns:
            op.add_column(TABLE, column)
    checks = {check["name"] for check in sa.inspect(op.get_bind()).get_check_constraints(TABLE)}
    if set(CHECKS) - checks:
        with op.batch_alter_table(TABLE) as batch:
            for name, expression in CHECKS.items():
                if name not in checks:
                    batch.create_check_constraint(name, expression)


def downgrade():
    # Removing review history would misrepresent approved data as an ordinary
    # reference. Restore a pre-upgrade backup instead of silently discarding it.
    if op.get_bind().scalar(sa.text(
        "SELECT count(*) FROM validation_dataset_items WHERE review_status != 'draft' "
        "OR source_ref IS NOT NULL OR source_label_id IS NOT NULL OR source_created_by IS NOT NULL"
    )):
        raise RuntimeError("ground_truth_downgrade_requires_pre_upgrade_backup")
    with op.batch_alter_table(TABLE) as batch:
        for name in CHECKS:
            batch.drop_constraint(name, type_="check")
        for name in ("review_status", "source_ref", "source_label_id", "source_created_by"):
            batch.drop_column(name)
