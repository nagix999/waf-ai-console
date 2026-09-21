"""Keep exact test candidate identity; historical runs remain unclassified."""
from alembic import op
import sqlalchemy as sa

revision = "0020_test_candidate_config"
down_revision = "0019_ground_truth_review"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("test_runs")}
    for column in (sa.Column("configuration_snapshot_json", sa.JSON()), sa.Column("configuration_hash", sa.String(64))):
        if column.name not in columns:
            op.add_column("test_runs", column)


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM test_runs WHERE configuration_hash IS NOT NULL")):
        raise RuntimeError("candidate_configuration_downgrade_requires_pre_upgrade_backup")
    op.drop_column("test_runs", "configuration_hash")
    op.drop_column("test_runs", "configuration_snapshot_json")
