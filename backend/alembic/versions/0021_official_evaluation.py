"""Approved-only evaluations, with immutable result records and retry identity."""
from alembic import op
import sqlalchemy as sa

revision = "0021_official_evaluation"
down_revision = "0020_test_candidate_config"
branch_labels = None
depends_on = None


def upgrade():
    definitions = {
        "test_runs": [sa.Column("evaluation_mode", sa.String(24), nullable=False, server_default="reference"),
            sa.Column("approved_item_version_ids", sa.JSON()),
            sa.Column("metrics_version", sa.String(64)),
            sa.Column("official_evaluation_pending", sa.Boolean(), nullable=False, server_default=sa.false())],
        "test_run_items": [sa.Column("dataset_item_version_id", sa.String(36),
            sa.ForeignKey("validation_dataset_items.id", ondelete="RESTRICT"))],
        "test_evaluations": [sa.Column("evaluation_kind", sa.String(24), nullable=False, server_default="reference"),
            sa.Column("metrics_version", sa.String(64)), sa.Column("configuration_hash", sa.String(64)),
            sa.Column("analysis_ids_json", sa.JSON()), sa.Column("summary_json", sa.JSON())],
    }
    for table, fields in definitions.items():
        names = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}
        for column in fields:
            if column.name not in names:
                # SQLite supports nullable REFERENCES on ADD COLUMN directly;
                # Alembic's separate FK operation does not. Keep parent tables.
                if table == "test_run_items" and op.get_bind().dialect.name == "sqlite":
                    op.execute(sa.text("ALTER TABLE test_run_items ADD COLUMN dataset_item_version_id VARCHAR(36) "
                                       "REFERENCES validation_dataset_items(id) ON DELETE RESTRICT"))
                else:
                    op.add_column(table, column)
    if "ix_test_runs_official_pending" not in {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("test_runs")}:
        op.create_index("ix_test_runs_official_pending", "test_runs", ["official_evaluation_pending", "id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM test_runs WHERE evaluation_mode != 'reference'")) or op.get_bind().scalar(
        sa.text("SELECT count(*) FROM test_evaluations WHERE evaluation_kind != 'reference'")):
        raise RuntimeError("official_evaluation_downgrade_requires_pre_upgrade_backup")
    op.drop_index("ix_test_runs_official_pending", table_name="test_runs")
    for table, columns in {"test_evaluations": ("evaluation_kind", "metrics_version", "configuration_hash", "analysis_ids_json", "summary_json"),
            "test_run_items": ("dataset_item_version_id",),
            "test_runs": ("evaluation_mode", "approved_item_version_ids", "metrics_version", "official_evaluation_pending")}.items():
        if table == "test_run_items":
            # Fresh metadata declares a table-level FK; SQLite DROP COLUMN
            # cannot remove that constraint. This leaf table can be rebuilt.
            with op.batch_alter_table(table) as batch:
                batch.drop_column("dataset_item_version_id")
        else:
            for column in columns:
                op.drop_column(table, column)
