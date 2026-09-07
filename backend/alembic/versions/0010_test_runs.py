"""Named test submissions and immutable item/reference membership."""
from alembic import op
import sqlalchemy as sa

revision = "0010_test_runs"
down_revision = "0009_model_validation_dataset"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    if "test_runs" not in tables:
        op.create_table("test_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("idempotency_key", sa.String(120), nullable=False, unique=True),
            sa.Column("request_hash", sa.String(64), nullable=False),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("source_system", sa.String(120), nullable=False, unique=True),
            sa.Column("filename", sa.String(255)), sa.Column("dataset_hash", sa.String(64)),
            sa.Column("model_test_run_id", sa.String(36), sa.ForeignKey("vllm_test_runs.id", ondelete="RESTRICT"), unique=True),
            sa.Column("profile_id", sa.String(36), sa.ForeignKey("vllm_profiles.id", ondelete="RESTRICT")),
            sa.Column("profile_fingerprint", sa.String(64)),
            sa.Column("profile_metadata", sa.JSON(), nullable=False),
            sa.Column("execution_mode", sa.String(32), nullable=False),
            sa.Column("prompt_snapshot_ciphertext", sa.Text(), nullable=False),
            sa.Column("prompt_policy_version_id", sa.String(36), sa.ForeignKey("prompt_policy_versions.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("prompt_version", sa.String(120), nullable=False),
            sa.Column("encryption_key_version", sa.String(64), nullable=False),
            sa.Column("created_by", sa.String(255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
        op.create_index("ix_test_runs_created", "test_runs", ["created_at"])
    if "test_run_items" not in tables:
        op.create_table("test_run_items",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("test_run_id", sa.String(36), sa.ForeignKey("test_runs.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("row_number", sa.Integer(), nullable=False),
            sa.Column("analysis_id", sa.String(36), sa.ForeignKey("analyses.id", ondelete="RESTRICT")),
            sa.Column("label_id", sa.String(36), sa.ForeignKey("analysis_labels.id", ondelete="RESTRICT")),
            sa.Column("event_id", sa.String(255)), sa.Column("difficulty", sa.String(80)),
            sa.Column("test_category", sa.String(120)), sa.Column("case_name", sa.String(240)),
            sa.Column("ingest_status", sa.String(24), nullable=False),
            sa.Column("error_code", sa.String(120)),
            sa.UniqueConstraint("test_run_id", "row_number", name="uq_test_run_row"))
        op.create_index("ix_test_run_items_analysis", "test_run_items", ["analysis_id"])
        op.create_index("ix_test_run_items_run", "test_run_items", ["test_run_id"])
    columns = {column["name"] for column in sa.inspect(connection).get_columns("vllm_test_runs")}
    for name, length in (("name", 120), ("idempotency_key", 120), ("request_hash", 64)):
        if name not in columns:
            op.add_column("vllm_test_runs", sa.Column(name, sa.String(length), nullable=True))
    if "uq_vllm_tests_idempotency" not in {item["name"] for item in sa.inspect(connection).get_indexes("vllm_test_runs")}:
        op.create_index("uq_vllm_tests_idempotency", "vllm_test_runs", ["idempotency_key"], unique=True)


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM test_runs")) or connection.scalar(sa.text("SELECT COUNT(*) FROM vllm_test_runs WHERE name IS NOT NULL")):
        raise RuntimeError("cannot_downgrade_with_named_test_history")
    op.drop_table("test_run_items")
    op.drop_table("test_runs")
    op.drop_index("uq_vllm_tests_idempotency", table_name="vllm_test_runs")
    # Leave nullable historical columns in SQLite instead of rebuilding a
    # referenced parent and risking cascade deletion. Upgrade is idempotent.
    if connection.dialect.name != "sqlite":
        for name in ("request_hash", "idempotency_key", "name"):
            op.drop_column("vllm_test_runs", name)
