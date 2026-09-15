"""Versioned validation data, manual references, Test API ownership."""
from alembic import op
import sqlalchemy as sa
from app.models import ValidationDataset, ValidationDatasetItem, ValidationDatasetVersion, TestEvaluation

revision = "0017_validation_data"
down_revision = "0016_evidence_editor"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for model in (ValidationDataset, ValidationDatasetItem, ValidationDatasetVersion, TestEvaluation):
        model.__table__.create(bind, checkfirst=True)
    additions = {
        "analyses": [sa.Column("internal_only", sa.Boolean(), nullable=False, server_default=sa.false())],
        "analysis_labels": [sa.Column("comment_ciphertext", sa.Text()), sa.Column("encryption_key_version", sa.String(64))],
        "service_api_keys": [sa.Column("purpose", sa.String(16), nullable=False, server_default="production")],
        "test_runs": [sa.Column("dataset_version_id", sa.String(36)), sa.Column("api_source_system", sa.String(120)),
                      sa.Column("accepting_items", sa.Boolean(), nullable=False, server_default=sa.false())],
    }
    for table, candidates in additions.items():
        columns = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        with op.batch_alter_table(table) as batch:
            for column in candidates:
                if column.name not in columns:
                    batch.add_column(column)
                    if column.name == "dataset_version_id":
                        batch.create_foreign_key("fk_test_dataset_version", "validation_dataset_versions", [column.name], ["id"], ondelete="RESTRICT")
                    if column.name == "api_source_system":
                        batch.create_index("ix_test_runs_api_source_system", [column.name])
            if table == "analysis_labels" and any(c["name"] == "ck_analysis_label_abstention" for c in sa.inspect(bind).get_check_constraints(table)):
                batch.drop_constraint("ck_analysis_label_abstention", type_="check")


def downgrade():
    bind = op.get_bind()
    for query in (
        "SELECT count(*) FROM validation_datasets", "SELECT count(*) FROM test_evaluations",
        "SELECT count(*) FROM analyses WHERE internal_only",
        "SELECT count(*) FROM service_api_keys WHERE purpose != 'production'",
        "SELECT count(*) FROM test_runs WHERE dataset_version_id IS NOT NULL OR api_source_system IS NOT NULL OR accepting_items",
        "SELECT count(*) FROM analysis_labels WHERE comment_ciphertext IS NOT NULL OR (verdict = 'inconclusive' AND source_kind = 'reference')",
    ):
        if bind.scalar(sa.text(query)):
            raise RuntimeError("validation_data_downgrade_requires_pre_upgrade_backup")
    op.drop_table("test_evaluations")
    with op.batch_alter_table("test_runs") as batch:
        for fk in sa.inspect(bind).get_foreign_keys("test_runs"):
            if fk["constrained_columns"] == ["dataset_version_id"] and fk["name"]:
                batch.drop_constraint(fk["name"], type_="foreignkey")
        batch.drop_index("ix_test_runs_api_source_system")
        batch.drop_column("dataset_version_id")
        batch.drop_column("api_source_system")
        batch.drop_column("accepting_items")
    for table in ("validation_dataset_versions", "validation_dataset_items", "validation_datasets"):
        op.drop_table(table)
    op.drop_column("analyses", "internal_only")
    op.drop_column("service_api_keys", "purpose")
    with op.batch_alter_table("analysis_labels") as batch:
        batch.drop_column("comment_ciphertext")
        batch.drop_column("encryption_key_version")
        batch.create_check_constraint("ck_analysis_label_abstention", "verdict != 'inconclusive' OR source_kind = 'synthetic_expected'")
