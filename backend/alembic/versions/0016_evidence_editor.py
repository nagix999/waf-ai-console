"""Optional evidence editor assignments and immutable named-test settings."""
from alembic import op
import sqlalchemy as sa

revision = "0016_evidence_editor"
down_revision = "0015_concurrency"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agent_configurations")}
    with op.batch_alter_table("agent_configurations") as batch:
        for purpose in ("production", "test"):
            name = purpose + "_evidence_editor_enabled"
            if name not in columns:
                batch.add_column(sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()))
            name = purpose + "_evidence_editor_profile_id"
            if name not in columns:
                batch.add_column(sa.Column(name, sa.String(36), nullable=True))
                batch.create_foreign_key("fk_" + name, "vllm_profiles", [name], ["id"], ondelete="RESTRICT")
    if "evidence_editor_snapshot_ciphertext" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("test_runs")}:
        op.add_column("test_runs", sa.Column("evidence_editor_snapshot_ciphertext", sa.Text(), nullable=True))


def downgrade():
    bind = op.get_bind()
    # New execution snapshots cannot safely be consumed by older workers.
    if (bind.scalar(sa.text("SELECT count(*) FROM analyses WHERE execution_snapshot_ciphertext IS NOT NULL"))
            or bind.scalar(sa.text("SELECT count(*) FROM test_runs WHERE evidence_editor_snapshot_ciphertext IS NOT NULL"))
            or bind.scalar(sa.text("SELECT count(*) FROM agent_configurations WHERE production_evidence_editor_enabled OR test_evidence_editor_enabled OR production_evidence_editor_profile_id IS NOT NULL OR test_evidence_editor_profile_id IS NOT NULL"))):
        raise RuntimeError("evidence_editor_downgrade_requires_pre_upgrade_backup")
    op.drop_column("test_runs", "evidence_editor_snapshot_ciphertext")
    with op.batch_alter_table("agent_configurations") as batch:
        for purpose in ("production", "test"):
            name = purpose + "_evidence_editor_profile_id"
            for fk in sa.inspect(bind).get_foreign_keys("agent_configurations"):
                if fk["constrained_columns"] == [name] and fk["name"]:
                    batch.drop_constraint(fk["name"], type_="foreignkey")
            batch.drop_column(name)
            batch.drop_column(purpose + "_evidence_editor_enabled")
