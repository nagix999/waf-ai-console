"""Immutable parser registry and encrypted admin diagnostics; no history rewrite."""
from alembic import op
import sqlalchemy as sa

revision = "0029_parser_management"
down_revision = "0028_review_categories"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("parser_versions"):
        op.create_table("parser_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("engine_version", sa.String(40), nullable=False),
            sa.Column("options_json", sa.JSON(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("parent_id", sa.String(36), sa.ForeignKey("parser_versions.id", ondelete="RESTRICT")),
            sa.Column("description_ciphertext", sa.Text()),
            sa.Column("created_by", sa.String(255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    if not sa.inspect(bind).has_table("parser_configuration"):
        op.create_table("parser_configuration",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("active_version_id", sa.String(36), sa.ForeignKey("parser_versions.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.CheckConstraint("id = 1", name="ck_parser_configuration_singleton"))
    if not sa.inspect(bind).has_table("parser_jobs"):
        op.create_table("parser_jobs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("kind", sa.String(24), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("baseline_version_id", sa.String(36), sa.ForeignKey("parser_versions.id", ondelete="RESTRICT")),
            sa.Column("candidate_version_id", sa.String(36), sa.ForeignKey("parser_versions.id", ondelete="RESTRICT")),
            sa.Column("profile_id", sa.String(36), sa.ForeignKey("vllm_profiles.id", ondelete="RESTRICT")),
            sa.Column("profile_fingerprint", sa.String(64)),
            sa.Column("request_ciphertext", sa.Text(), nullable=False),
            sa.Column("result_ciphertext", sa.Text()),
            sa.Column("encryption_key_version", sa.String(64), nullable=False),
            sa.Column("summary_json", sa.JSON(), nullable=False),
            sa.Column("error_code", sa.String(120)),
            sa.Column("lease_owner", sa.String(255)),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
            sa.Column("created_by", sa.String(255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint("kind IN ('comparison', 'suggestion')", name="ck_parser_job_kind"),
            sa.CheckConstraint("status IN ('pending', 'running', 'completed', 'failed')", name="ck_parser_job_status"))
        op.create_index("ix_parser_jobs_status_created", "parser_jobs", ["status", "created_at"])
    for table in ("analyses", "test_runs", "vllm_test_runs", "test_configuration_defaults"):
        columns = {column["name"] for column in sa.inspect(bind).get_columns(table)}
        if "parser_version_id" not in columns:
            if bind.dialect.name == "sqlite":
                # Never rebuild these populated parents: cascade history must survive.
                op.execute(f"ALTER TABLE {table} ADD COLUMN parser_version_id VARCHAR(36) REFERENCES parser_versions(id) ON DELETE RESTRICT")
            else:
                op.add_column(table, sa.Column("parser_version_id", sa.String(36), sa.ForeignKey("parser_versions.id", ondelete="RESTRICT")))
        if table != "test_configuration_defaults" and "parser_snapshot_json" not in columns:
            op.add_column(table, sa.Column("parser_snapshot_json", sa.JSON()))


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM parser_jobs")) or bind.scalar(sa.text(
            "SELECT COUNT(*) FROM parser_versions WHERE id <> 'generic-http-v3'")):
        raise RuntimeError("parser_history_requires_current_schema")
    for table in ("analyses", "test_runs", "vllm_test_runs", "test_configuration_defaults"):
        if bind.scalar(sa.text(f"SELECT COUNT(*) FROM {table} WHERE parser_version_id IS NOT NULL")):
            raise RuntimeError("parser_snapshots_require_current_schema")
    # Retain unused nullable structures on SQLite rather than rebuild parents.
    # Re-upgrade is idempotent. Actual downgrade requires a pre-upgrade backup.
