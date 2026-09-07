"""Optional, durable 150-event model validation, separated from production."""
from alembic import op
import sqlalchemy as sa


revision = "0009_model_validation_dataset"
down_revision = "0008_service_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    columns = {item["name"] for item in sa.inspect(connection).get_columns("vllm_test_runs")}
    if "include_dataset" not in columns:
        op.add_column("vllm_test_runs", sa.Column("include_dataset", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "dataset_version" not in columns:
        op.add_column("vllm_test_runs", sa.Column("dataset_version", sa.String(80)))
    if "dataset_hash" not in columns:
        op.add_column("vllm_test_runs", sa.Column("dataset_hash", sa.String(64)))
    if "model_test_run_id" not in {item["name"] for item in sa.inspect(connection).get_columns("analyses")}:
        # SQLite supports a nullable REFERENCES column without rebuilding old
        # data. Alembic's separate ADD CONSTRAINT is not supported by SQLite.
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql(
                "ALTER TABLE analyses ADD COLUMN model_test_run_id VARCHAR(36) "
                "REFERENCES vllm_test_runs(id) ON DELETE RESTRICT"
            )
        else:
            op.add_column("analyses", sa.Column("model_test_run_id", sa.String(36)))
            op.create_foreign_key("fk_analyses_model_test_run", "analyses", "vllm_test_runs", ["model_test_run_id"], ["id"], ondelete="RESTRICT")
        op.create_index("ix_analyses_model_test_run_id", "analyses", ["model_test_run_id"])


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM analyses WHERE model_test_run_id IS NOT NULL")) or connection.scalar(sa.text("SELECT COUNT(*) FROM vllm_test_runs WHERE include_dataset = 1")):
        raise RuntimeError("cannot_downgrade_with_model_validation_history")
    # Fresh databases have a table-level FK; removing it requires a SQLite
    # table rebuild. Never rebuild a populated parent: incoming ON DELETE
    # CASCADE constraints could erase agent/review/label history.
    if connection.scalar(sa.text("SELECT COUNT(*) FROM analyses")):
        raise RuntimeError("cannot_downgrade_model_validation_with_analysis_history")
    op.drop_index("ix_analyses_model_test_run_id", table_name="analyses")
    if connection.dialect.name == "sqlite":
        convention = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
        key = next(key for key in sa.inspect(connection).get_foreign_keys("analyses") if key["constrained_columns"] == ["model_test_run_id"])
        name = key["name"] or "fk_analyses_model_test_run_id_vllm_test_runs"
        with op.batch_alter_table("analyses", naming_convention=convention) as batch:
            batch.drop_constraint(name, type_="foreignkey")
            batch.drop_column("model_test_run_id")
    else:
        op.drop_constraint("fk_analyses_model_test_run", "analyses", type_="foreignkey")
        op.drop_column("analyses", "model_test_run_id")
    for name in ("dataset_hash", "dataset_version", "include_dataset"):
        op.drop_column("vllm_test_runs", name)
