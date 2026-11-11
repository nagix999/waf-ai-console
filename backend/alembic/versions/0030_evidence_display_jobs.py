"""Independent encrypted evidence presentation; retain all existing results."""
from alembic import op
import sqlalchemy as sa

revision = "0030_evidence_display_jobs"
down_revision = "0029_parser_management"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("evidence_display_jobs"):
        op.create_table("evidence_display_jobs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("analysis_id", sa.String(36), sa.ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("snapshot_ciphertext", sa.Text(), nullable=False),
            sa.Column("input_ciphertext", sa.Text()), sa.Column("output_ciphertext", sa.Text()),
            sa.Column("source_hash", sa.String(64)), sa.Column("encryption_key_version", sa.String(64), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False), sa.Column("error_code", sa.String(120)),
            sa.Column("attempt_count", sa.Integer(), nullable=False), sa.Column("lease_owner", sa.String(255)),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint("status IN ('pending', 'running', 'completed', 'skipped', 'failed', 'canceled')",
                               name="ck_evidence_display_job_status"))
        op.create_index("ix_evidence_display_jobs_status_created", "evidence_display_jobs", ["status", "created_at"])
    if "display_editor_snapshot_ciphertext" not in {c["name"] for c in sa.inspect(bind).get_columns("test_runs")}:
        # Nullable additive column: do not rebuild populated cascade parents.
        op.add_column("test_runs", sa.Column("display_editor_snapshot_ciphertext", sa.Text()))


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM evidence_display_jobs")) or bind.scalar(sa.text(
            "SELECT COUNT(*) FROM test_runs WHERE display_editor_snapshot_ciphertext IS NOT NULL")):
        raise RuntimeError("evidence_display_history_requires_current_schema")
    # Keep additive structures on SQLite; never rebuild populated parents.
