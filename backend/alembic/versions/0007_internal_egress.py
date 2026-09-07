"""Explicit internal IP/port permissions; never import environment allowlists."""
from alembic import op
import sqlalchemy as sa


revision = "0007_internal_egress"
down_revision = "0006_prompt_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 uses current ORM metadata on a fresh installation.
    if sa.inspect(op.get_bind()).has_table("internal_egress_targets"):
        return
    op.create_table(
        "internal_egress_targets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ip_address", sa.String(39), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ip_address", "port", name="uq_internal_egress_ip_port"),
        sa.CheckConstraint("port >= 1 AND port <= 65535", name="ck_internal_egress_port"),
        sa.CheckConstraint("revision >= 1", name="ck_internal_egress_revision"),
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM internal_egress_targets")):
        raise RuntimeError("cannot_downgrade_with_internal_egress_configuration")
    op.drop_table("internal_egress_targets")
