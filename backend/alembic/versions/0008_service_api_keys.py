"""Permanent, individually revocable hashed service API credentials."""
from alembic import op
import sqlalchemy as sa


revision = "0008_service_api_keys"
down_revision = "0007_internal_egress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("service_api_keys"):
        return
    op.create_table(
        "service_api_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("key_prefix", sa.String(48), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("source_system", sa.String(120), nullable=False),
        sa.Column("scopes_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by", sa.String(255)),
        sa.UniqueConstraint("name", name="uq_service_api_key_name"),
        sa.UniqueConstraint("key_hash", name="uq_service_api_key_hash"),
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM service_api_keys")):
        raise RuntimeError("cannot_downgrade_with_service_api_key_history")
    op.drop_table("service_api_keys")
