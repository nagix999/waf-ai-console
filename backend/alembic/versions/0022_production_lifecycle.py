"""Production promotion, immutable changes and content-free worker heartbeats.

Existing assignments/data are unchanged. Baseline is captured transactionally
by the application once encryption keys and the deployed rules are available.
"""
from alembic import op
import sqlalchemy as sa

revision = "0022_production_lifecycle"
down_revision = "0021_official_evaluation"
branch_labels = None
depends_on = None


def upgrade():
    # 0001 historically calls current Base.metadata.create_all on fresh installs.
    # Real upgrades do not have these tables; fresh installs may already do.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    def create(name, *columns):
        if name not in existing:
            op.create_table(name, *columns)
    create("production_promotions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_test_run_id", sa.String(36), sa.ForeignKey("test_runs.id", ondelete="RESTRICT")),
        sa.Column("evaluation_id", sa.String(36), sa.ForeignKey("test_evaluations.id", ondelete="RESTRICT")),
        sa.Column("previous_configuration_hash", sa.String(64)),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    create("change_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("resource_type", sa.String(80), nullable=False),
        sa.Column("resource_id", sa.String(255), nullable=False),
        sa.Column("before_json", sa.JSON()), sa.Column("after_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("change_events")}
    if "ix_change_events_created_at" not in indexes:
        op.create_index("ix_change_events_created_at", "change_events", ["created_at"])
    create("worker_heartbeats",
        sa.Column("worker_id", sa.String(255), primary_key=True),
        sa.Column("worker_kind", sa.String(24), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False))
    if op.get_bind().dialect.name == "sqlite":
        for table in ("production_promotions", "change_events"):
            for action in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable_history'); END")


def downgrade():
    for table in ("production_promotions", "change_events"):
        if op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar():
            raise RuntimeError("lifecycle_history_must_be_preserved")
    for table in ("worker_heartbeats", "change_events", "production_promotions"):
        op.drop_table(table)
