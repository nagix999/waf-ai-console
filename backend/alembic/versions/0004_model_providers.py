"""Add explicitly approved OpenAI profiles while preserving legacy vLLM data."""

from alembic import op
import sqlalchemy as sa


revision = "0004_model_providers"
down_revision = "0003_analysis_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("vllm_profiles")}
    # 0001 creates current metadata for a fresh database; old databases only
    # receive additive columns. No key, result, status or fingerprint is updated.
    if "provider" not in columns:
        op.add_column("vllm_profiles", sa.Column("provider", sa.String(20), nullable=False, server_default="vllm"))
    if "external_data_approved" not in columns:
        op.add_column("vllm_profiles", sa.Column("external_data_approved", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    bind = op.get_bind()
    # Silently dropping these fields could reinterpret an external profile and
    # its stored key as vLLM. Require an explicit backup/provider conversion first.
    unsafe = bind.scalar(sa.text(
        "SELECT COUNT(*) FROM vllm_profiles WHERE provider != 'vllm' OR external_data_approved = true"
    ))
    if unsafe:
        raise RuntimeError("cannot_downgrade_model_providers_with_external_profiles")
    op.drop_column("vllm_profiles", "external_data_approved")
    op.drop_column("vllm_profiles", "provider")
