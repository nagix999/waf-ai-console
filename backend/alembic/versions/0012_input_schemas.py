"""Immutable input definitions and encrypted, admission-time field snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "0012_input_schemas"
down_revision = "0011_model_test_role"
branch_labels = None
depends_on = None

TARGETS = ("analyses", "test_runs", "vllm_test_runs")


def upgrade():
    bind = op.get_bind()
    def create(name, *columns):
        if not sa.inspect(bind).has_table(name):
            op.create_table(name, *columns)
    create("input_schema_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("change_note", sa.String(1000), nullable=False),
        sa.Column("parent_id", sa.String(36), sa.ForeignKey("input_schema_versions.id", ondelete="RESTRICT")),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("definition_ciphertext", sa.Text(), nullable=False),
        sa.Column("encryption_key_version", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("version_number", name="uq_input_schema_version_number"),
        sa.CheckConstraint("version_number > 0", name="ck_input_schema_version_number"))
    create("input_schema_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("active_version_id", sa.String(36), sa.ForeignKey("input_schema_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_input_schema_state_singleton"),
        sa.CheckConstraint("revision >= 1", name="ck_input_schema_state_revision"))
    create("input_schema_activations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("previous_version_id", sa.String(36), sa.ForeignKey("input_schema_versions.id", ondelete="RESTRICT")),
        sa.Column("active_version_id", sa.String(36), sa.ForeignKey("input_schema_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("revision", name="uq_input_schema_activation_revision"))
    for table in TARGETS:
        columns = {column["name"] for column in sa.inspect(bind).get_columns(table)}
        if "input_schema_snapshot_ciphertext" not in columns:
            op.add_column(table, sa.Column("input_schema_snapshot_ciphertext", sa.Text()))
        if "input_schema_version_id" in columns:
            continue
        # Never rebuild a populated parent with cascading history on SQLite.
        if bind.dialect.name == "sqlite":
            op.execute(f"ALTER TABLE {table} ADD COLUMN input_schema_version_id VARCHAR(36) REFERENCES input_schema_versions(id) ON DELETE RESTRICT")
        else:
            op.add_column(table, sa.Column("input_schema_version_id", sa.String(36)))
            op.create_foreign_key(f"fk_{table}_input_schema_version", table, "input_schema_versions", ["input_schema_version_id"], ["id"], ondelete="RESTRICT")


def downgrade():
    bind = op.get_bind()
    for table in ("input_schema_versions", "input_schema_activations"):
        if bind.scalar(sa.text(f"SELECT COUNT(*) FROM {table}")):
            raise RuntimeError("cannot_downgrade_with_input_schema_history")
    for table in TARGETS:
        if bind.scalar(sa.text(f"SELECT COUNT(*) FROM {table} WHERE input_schema_version_id IS NOT NULL OR input_schema_snapshot_ciphertext IS NOT NULL")):
            raise RuntimeError("cannot_downgrade_with_input_schema_snapshots")
    if bind.dialect.name == "sqlite":
        # Retain unused nullable columns/tables, avoiding parent reconstruction.
        # A subsequent upgrade reuses them; no historical rows are modified.
        return
    for table in TARGETS:
        op.drop_constraint(f"fk_{table}_input_schema_version", table, type_="foreignkey")
        op.drop_column(table, "input_schema_version_id")
        op.drop_column(table, "input_schema_snapshot_ciphertext")
    op.drop_table("input_schema_activations")
    op.drop_table("input_schema_state")
    op.drop_table("input_schema_versions")
