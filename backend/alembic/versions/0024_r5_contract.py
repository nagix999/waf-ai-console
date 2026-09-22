"""R5 admission metadata, test defaults and draft provenance. No history rewrites."""
from alembic import op
import sqlalchemy as sa

revision = "0024_r5_contract"
down_revision = "0023_ground_truth_working"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    additions = {
        "analyses": [sa.Column("initial_verdict", sa.String(32)), sa.Column("initial_probability", sa.Float()),
                     sa.Column("initial_model_version", sa.String(255))],
        "test_runs": [sa.Column("test_purpose", sa.String(32), nullable=False, server_default="legacy_unknown")],
        "validation_dataset_working_items": [
            sa.Column("reference_origin", sa.String(32), nullable=False, server_default="none"),
            sa.Column("reference_origin_ref_id", sa.String(36)),
            sa.Column("provenance_json", sa.JSON(), nullable=False, server_default="{}")],
    }
    for table, columns in additions.items():
        existing = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        for column in columns:
            if column.name not in existing:
                op.add_column(table, column)
    from app.models import TestConfigurationDefaults, GroundTruthImportPreview
    TestConfigurationDefaults.__table__.create(bind, checkfirst=True)
    GroundTruthImportPreview.__table__.create(bind, checkfirst=True)


def downgrade():
    # An unused migration can be rolled back; recorded R5 history cannot.
    bind = op.get_bind()
    for query in (
        "SELECT count(*) FROM analyses WHERE initial_verdict IS NOT NULL OR initial_probability IS NOT NULL OR initial_model_version IS NOT NULL",
        "SELECT count(*) FROM test_runs WHERE test_purpose != 'legacy_unknown'",
        "SELECT count(*) FROM validation_dataset_working_items WHERE reference_origin != 'none' OR reference_origin_ref_id IS NOT NULL OR provenance_json != '{}'",
        "SELECT count(*) FROM test_configuration_defaults",
        "SELECT count(*) FROM ground_truth_import_previews",
    ):
        if bind.scalar(sa.text(query)):
            raise RuntimeError("r5_history_requires_pre_upgrade_backup")
    op.drop_table("ground_truth_import_previews")
    op.drop_table("test_configuration_defaults")
    for table, columns in {
        "analyses": ("initial_verdict", "initial_probability", "initial_model_version"),
        "test_runs": ("test_purpose",),
        "validation_dataset_working_items": ("reference_origin", "reference_origin_ref_id", "provenance_json"),
    }.items():
        for column in columns:
            op.drop_column(table, column)
