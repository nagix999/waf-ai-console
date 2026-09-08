"""Attributed admission, retained key deletion, immutable failed-analysis retries.

SQLite's former table UNIQUE requires one offline table reconstruction. This
migration NEVER disables foreign keys on a live application connection. Stop
workers and use Alembic's dedicated connection (foreign_keys=OFF), with a backup.
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_analysis_retries_keys"
down_revision = "0012_input_schemas"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    sqlite = bind.dialect.name == "sqlite"
    constraints = sa.inspect(bind).get_unique_constraints("analyses")
    rebuild = any(item["name"] == "uq_analysis_source_event" for item in constraints)
    if sqlite and rebuild and bind.scalar(sa.text("PRAGMA foreign_keys")):
        raise RuntimeError("analysis_retry_migration_requires_offline_foreign_keys_disabled")
    if sqlite and bind.execute(sa.text("PRAGMA foreign_key_check")).first():
        raise RuntimeError("analysis_retry_migration_existing_foreign_key_violation")
    quote = bind.dialect.identifier_preparer.quote
    before_counts = {table: bind.scalar(sa.text(f"SELECT COUNT(*) FROM {quote(table)}"))
                     for table in sa.inspect(bind).get_table_names()}
    key_columns = {item["name"] for item in sa.inspect(bind).get_columns("service_api_keys")}
    for column in (sa.Column("deleted_at", sa.DateTime(timezone=True)), sa.Column("deleted_by", sa.String(255))):
        if column.name not in key_columns:
            op.add_column("service_api_keys", column)
    columns = {item["name"] for item in sa.inspect(bind).get_columns("analyses")}
    with op.batch_alter_table("analyses", recreate="always" if sqlite and rebuild else "auto") as batch:
        if rebuild:
            batch.drop_constraint("uq_analysis_source_event", type_="unique")
        if "service_api_key_id" not in columns:
            batch.add_column(sa.Column("service_api_key_id", sa.String(36)))
            batch.create_foreign_key("fk_analysis_service_key", "service_api_keys", ["service_api_key_id"], ["id"], ondelete="RESTRICT")
            batch.create_index("ix_analyses_service_api_key_id", ["service_api_key_id"])
        if "retry_of_analysis_id" not in columns:
            batch.add_column(sa.Column("retry_of_analysis_id", sa.String(36)))
            batch.create_foreign_key("fk_analysis_retry_of", "analyses", ["retry_of_analysis_id"], ["id"], ondelete="RESTRICT")
            batch.create_unique_constraint("uq_analysis_retry_of", ["retry_of_analysis_id"])
        if "retry_idempotency_key" not in columns:
            batch.add_column(sa.Column("retry_idempotency_key", sa.String(120)))
            batch.create_unique_constraint("uq_analysis_retry_idempotency", ["retry_idempotency_key"])
        if "execution_snapshot_ciphertext" not in columns:
            batch.add_column(sa.Column("execution_snapshot_ciphertext", sa.Text()))
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("analyses")}
    if "uq_analysis_source_event_original" not in indexes:
        op.create_index("uq_analysis_source_event_original", "analyses", ["source_system", "event_id"], unique=True,
                        sqlite_where=sa.text("retry_of_analysis_id IS NULL"), postgresql_where=sa.text("retry_of_analysis_id IS NULL"))
    if sqlite and bind.execute(sa.text("PRAGMA foreign_key_check")).first():
        raise RuntimeError("analysis_retry_migration_foreign_key_violation")
    for table, count in before_counts.items():
        if bind.scalar(sa.text(f"SELECT COUNT(*) FROM {quote(table)}")) != count:
            raise RuntimeError("analysis_retry_migration_existing_history_count_changed")


def downgrade():
    # An older worker would execute pinned retries with today's model. Preserve
    # both history and this version boundary; restore a pre-upgrade backup if
    # an operational rollback is required.
    bind = op.get_bind()
    if (bind.scalar(sa.text("SELECT COUNT(*) FROM analyses WHERE retry_of_analysis_id IS NOT NULL OR service_api_key_id IS NOT NULL OR execution_snapshot_ciphertext IS NOT NULL"))
            or bind.scalar(sa.text("SELECT COUNT(*) FROM service_api_keys WHERE deleted_at IS NOT NULL"))):
        raise RuntimeError("analysis_retry_downgrade_requires_pre_upgrade_backup")
    # With no new provenance/history there are no duplicate source/event rows.
    # Retain unused nullable additions, as prior migrations do, rather than
    # rebuilding cascading parents a second time. Re-upgrade is idempotent.
