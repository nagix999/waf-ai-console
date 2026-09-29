"""Non-destructive ordinary TestRun stop metadata; canceled is not a failure."""
from alembic import op
import sqlalchemy as sa

revision = "0026_test_run_stop"
down_revision = "0025_analysis_read_index"
branch_labels = None
depends_on = None


def upgrade():
    # 0001 bootstraps current metadata on a fresh installation.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("test_runs")}
    for name, kind in (("stopped_at", sa.DateTime(timezone=True)), ("stopped_by", sa.String(255))):
        if name not in columns:
            op.add_column("test_runs", sa.Column(name, kind, nullable=True))


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM test_runs WHERE stopped_at IS NOT NULL OR stopped_by IS NOT NULL")) or bind.scalar(
            sa.text("SELECT COUNT(*) FROM analyses WHERE status = 'canceled'")):
        raise RuntimeError("test_stop_history_requires_current_schema")
    op.drop_column("test_runs", "stopped_by")
    op.drop_column("test_runs", "stopped_at")
