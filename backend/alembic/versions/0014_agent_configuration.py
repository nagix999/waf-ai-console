"""Independent role profiles; existing Primary assignments/history stay intact."""
from alembic import op
import sqlalchemy as sa

revision = "0014_agent_configuration"
down_revision = "0013_analysis_retries_keys"
branch_labels = None
depends_on = None


def upgrade():
    if "agent_configurations" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table("agent_configurations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("production_verifier_profile_id", sa.String(36), sa.ForeignKey("vllm_profiles.id", ondelete="RESTRICT")),
            sa.Column("test_verifier_profile_id", sa.String(36), sa.ForeignKey("vllm_profiles.id", ondelete="RESTRICT")),
            sa.CheckConstraint("id = 1", name="ck_agent_configuration_singleton"))
    # An absent row means both Verifiers explicitly inherit the existing Primary.


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM agent_configurations WHERE revision > 0")):
        raise RuntimeError("agent_configuration_downgrade_requires_pre_upgrade_backup")
    # A v2 execution snapshot must never run under a single-profile worker.
    if bind.scalar(sa.text("SELECT COUNT(*) FROM analyses WHERE execution_snapshot_ciphertext IS NOT NULL")):
        raise RuntimeError("agent_configuration_downgrade_requires_pre_upgrade_backup")
    op.drop_table("agent_configurations")
