"""Record analysis purpose, ingestion provenance and searchable result fields."""

from alembic import op
import sqlalchemy as sa

revision = "0003_analysis_search"
down_revision = "0002_vllm_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("analyses")}
    additions = [
        sa.Column("analysis_purpose", sa.String(32), nullable=False, server_default="legacy_unknown"),
        sa.Column("ingest_channel", sa.String(32), nullable=False, server_default="legacy_unknown"),
        sa.Column("event_fingerprint", sa.String(64), nullable=True),
        sa.Column("severity", sa.String(16), nullable=True),
        sa.Column("threat_category", sa.String(120), nullable=True),
    ]
    # 0001 uses current metadata on fresh databases; existing 0002 databases need these additions.
    for column in additions:
        if column.name not in columns:
            op.add_column("analyses", column)

    analyses = sa.table(
        "analyses", sa.column("id", sa.String), sa.column("result_json", sa.JSON),
        sa.column("severity", sa.String), sa.column("threat_category", sa.String),
    )
    valid_severities = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE", "UNKNOWN"}
    rows = bind.execute(sa.select(analyses.c.id, analyses.c.result_json, analyses.c.severity, analyses.c.threat_category))
    for row in rows:
        result = row.result_json
        threat = result.get("threat_analysis") if isinstance(result, dict) else None
        if not isinstance(threat, dict):
            continue
        values = {}
        severity = threat.get("severity")
        if row.severity is None and isinstance(severity, str) and severity in valid_severities:
            values["severity"] = severity
        category = threat.get("category")
        if row.threat_category is None and isinstance(category, str) and 0 < len(category) <= 120:
            values["threat_category"] = category
        if values:
            bind.execute(analyses.update().where(analyses.c.id == row.id).values(**values))

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("analyses")}
    for name, fields in (
        ("ix_analyses_purpose_created", ["analysis_purpose", "created_at"]),
        ("ix_analyses_severity_created", ["severity", "created_at"]),
        ("ix_analyses_category_created", ["threat_category", "created_at"]),
    ):
        if name not in indexes:
            op.create_index(name, "analyses", fields)


def downgrade() -> None:
    for name in ("ix_analyses_purpose_created", "ix_analyses_severity_created", "ix_analyses_category_created"):
        op.drop_index(name, table_name="analyses")
    with op.batch_alter_table("analyses") as batch:
        for name in ("analysis_purpose", "ingest_channel", "event_fingerprint", "severity", "threat_category"):
            batch.drop_column(name)
