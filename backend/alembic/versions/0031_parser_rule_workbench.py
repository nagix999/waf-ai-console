"""Encrypted vendor parser workbench; existing execution history is untouched."""
from alembic import op
import sqlalchemy as sa

revision = "0031_parser_rule_workbench"
down_revision = "0030_evidence_display_jobs"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("parser_workbenches"):
        op.create_table("parser_workbenches",
            sa.Column("id", sa.String(36), primary_key=True), sa.Column("name", sa.String(120), nullable=False),
            sa.Column("status", sa.String(24), nullable=False), sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("source_kind", sa.String(24), nullable=False),
            sa.Column("dataset_version_id", sa.String(36), sa.ForeignKey("validation_dataset_versions.id", ondelete="RESTRICT")),
            sa.Column("baseline_version_id", sa.String(36), sa.ForeignKey("parser_versions.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("candidate_version_id", sa.String(36), sa.ForeignKey("parser_versions.id", ondelete="RESTRICT")),
            sa.Column("input_ciphertext", sa.Text(), nullable=False), sa.Column("state_ciphertext", sa.Text(), nullable=False),
            sa.Column("encryption_key_version", sa.String(64), nullable=False), sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_by", sa.String(255), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("status IN ('draft', 'discovered', 'groups_confirmed', 'generated', 'validated', 'saved')",
                               name="ck_parser_workbench_status"))
        op.create_index("ix_parser_workbenches_updated_id", "parser_workbenches", ["updated_at", "id"])
    if not sa.inspect(bind).has_table("parser_workbench_jobs"):
        op.create_table("parser_workbench_jobs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("workbench_id", sa.String(36), sa.ForeignKey("parser_workbenches.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("kind", sa.String(24), nullable=False), sa.Column("status", sa.String(24), nullable=False),
            sa.Column("session_revision", sa.Integer(), nullable=False),
            sa.Column("profile_id", sa.String(36), sa.ForeignKey("vllm_profiles.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("profile_fingerprint", sa.String(64), nullable=False), sa.Column("request_ciphertext", sa.Text(), nullable=False),
            sa.Column("result_ciphertext", sa.Text()), sa.Column("encryption_key_version", sa.String(64), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False), sa.Column("error_code", sa.String(120)),
            sa.Column("lease_owner", sa.String(255)), sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
            sa.Column("created_by", sa.String(255), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint("kind IN ('format_discovery', 'rule_generation')", name="ck_parser_workbench_job_kind"),
            sa.CheckConstraint("status IN ('pending', 'running', 'completed', 'failed', 'cancelled')", name="ck_parser_workbench_job_status"))
        op.create_index("ix_parser_workbench_jobs_status_created", "parser_workbench_jobs", ["status", "created_at"])
        op.create_index("ix_parser_workbench_jobs_workbench_created", "parser_workbench_jobs", ["workbench_id", "created_at"])


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM parser_workbenches")) or bind.scalar(sa.text("SELECT COUNT(*) FROM parser_workbench_jobs")):
        raise RuntimeError("parser_workbench_history_requires_current_schema")
    # Additive SQLite tables remain; never rebuild populated execution parents.
