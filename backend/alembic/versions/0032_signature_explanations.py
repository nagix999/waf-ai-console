"""Immutable signature reference catalogs; preserve every existing execution."""
from alembic import op
import sqlalchemy as sa

revision = "0032_signature_explanations"
down_revision = "0031_parser_rule_workbench"
branch_labels = None
depends_on = None

TARGET_TABLES = ("analyses", "test_runs", "vllm_test_runs", "test_configuration_defaults")


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("signature_explanation_versions"):
        op.create_table("signature_explanation_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("version_no", sa.Integer(), nullable=False, unique=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("change_reason", sa.String(1000), nullable=False),
            sa.Column("parent_id", sa.String(36), sa.ForeignKey("signature_explanation_versions.id", ondelete="RESTRICT")),
            sa.Column("fingerprint", sa.String(64), nullable=False),
            sa.Column("entry_count", sa.Integer(), nullable=False),
            sa.Column("definition_ciphertext", sa.Text(), nullable=False),
            sa.Column("encryption_key_version", sa.String(64), nullable=False),
            sa.Column("created_by", sa.String(255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
        op.create_index("ix_signature_explanations_created_id", "signature_explanation_versions", ["created_at", "id"])
    if not sa.inspect(bind).has_table("signature_explanation_configuration"):
        op.create_table("signature_explanation_configuration",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("active_version_id", sa.String(36), sa.ForeignKey("signature_explanation_versions.id", ondelete="RESTRICT")),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.CheckConstraint("id = 1", name="ck_signature_explanation_configuration_singleton"))
        op.execute(sa.text("INSERT INTO signature_explanation_configuration (id, active_version_id, revision) VALUES (1, NULL, 0)"))
    for table in TARGET_TABLES:
        columns = {column["name"] for column in sa.inspect(bind).get_columns(table)}
        if "signature_explanation_version_id" not in columns:
            if bind.dialect.name == "sqlite":
                # Avoid populated-parent rebuilds and ON DELETE CASCADE losses.
                op.execute(f"ALTER TABLE {table} ADD COLUMN signature_explanation_version_id VARCHAR(36) REFERENCES signature_explanation_versions(id) ON DELETE RESTRICT")
            else:
                op.add_column(table, sa.Column("signature_explanation_version_id", sa.String(36),
                    sa.ForeignKey("signature_explanation_versions.id", ondelete="RESTRICT")))
        if table != "test_configuration_defaults" and "signature_explanation_snapshot_ciphertext" not in columns:
            op.add_column(table, sa.Column("signature_explanation_snapshot_ciphertext", sa.Text()))


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM signature_explanation_versions")):
        raise RuntimeError("signature_explanation_history_requires_current_schema")
    for table in TARGET_TABLES:
        condition = "signature_explanation_version_id IS NOT NULL"
        if table != "test_configuration_defaults":
            condition += " OR signature_explanation_snapshot_ciphertext IS NOT NULL"
        if bind.scalar(sa.text(f"SELECT COUNT(*) FROM {table} WHERE {condition}")):
            raise RuntimeError("signature_explanation_snapshots_require_current_schema")
    # Retain nullable structures to avoid rebuilding cascading parent tables.
    # A pre-upgrade backup is required for actual schema removal.
