"""Shared analysis limits and bounded LLM reservations; no history rewrite."""
from alembic import op
import sqlalchemy as sa

revision = "0015_concurrency"
down_revision = "0014_agent_configuration"
branch_labels = None
depends_on = None


def upgrade():
    # 0001 uses current metadata on a fresh installation; existing deployments
    # reach this revision without these tables. Support both paths.
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "concurrency_configuration" not in tables:
        op.create_table("concurrency_configuration",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("production", sa.Integer(), nullable=False),
            sa.Column("test", sa.Integer(), nullable=False),
            sa.Column("server_limits", sa.JSON(), nullable=False),
            sa.CheckConstraint("id = 1", name="ck_concurrency_singleton"),
            sa.CheckConstraint("production >= 1 AND production <= 32 AND test >= 1 AND test <= 32", name="ck_concurrency_range"))
    if "llm_call_slots" not in tables:
        op.create_table("llm_call_slots",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("server_key", sa.String(255), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
        op.create_index("ix_llm_slots_server_expires", "llm_call_slots", ["server_key", "expires_at"])


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM llm_call_slots")) or bind.scalar(sa.text(
            "SELECT COUNT(*) FROM concurrency_configuration WHERE revision > 0")):
        raise RuntimeError("concurrency_downgrade_requires_pre_upgrade_backup")
    op.drop_index("ix_llm_slots_server_expires", table_name="llm_call_slots")
    op.drop_table("llm_call_slots")
    op.drop_table("concurrency_configuration")
