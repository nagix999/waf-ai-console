"""Nullable admission provenance, without guessing historical Production IDs."""
import re

from alembic import op
import sqlalchemy as sa

revision = "0033_feedback_provenance"
down_revision = "0032_signature_explanations"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("analyses")}
    if "production_configuration_id" not in columns:
        if bind.dialect.name == "sqlite":
            # Rebuilding this populated parent can cascade-delete its reviews.
            op.execute("ALTER TABLE analyses ADD COLUMN production_configuration_id VARCHAR(36) "
                       "REFERENCES production_promotions(id) ON DELETE RESTRICT")
        else:
            op.add_column("analyses", sa.Column("production_configuration_id", sa.String(36),
                sa.ForeignKey("production_promotions.id", ondelete="RESTRICT")))
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("analyses")}
    if "ix_analyses_production_configuration_id" not in indexes:
        op.create_index("ix_analyses_production_configuration_id", "analyses", ["production_configuration_id"])
    # The old snapshots have no explicit ProductionPromotion ID. A matching
    # prompt, hash or admission time does not establish that missing identity.


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM analyses WHERE production_configuration_id IS NOT NULL")):
        raise RuntimeError("production_provenance_requires_current_schema")
    table_constraint = False
    if bind.dialect.name == "sqlite":
        definition = bind.scalar(sa.text("SELECT sql FROM sqlite_master WHERE type='table' AND name='analyses'"))
        # 0001's current-metadata fresh installs declare a table-level FK;
        # existing installations receive an inline FK via ADD COLUMN. SQLite
        # can natively drop only the latter. Never rebuild a populated parent.
        table_constraint = bool(re.search(r'FOREIGN\s+KEY\s*\(\s*["`\[]?production_configuration_id', definition or "", re.I))
        if table_constraint and bind.scalar(sa.text("SELECT COUNT(*) FROM analyses")):
            raise RuntimeError("production_provenance_downgrade_requires_pre_upgrade_backup")
    if "ix_analyses_production_configuration_id" in {
        index["name"] for index in sa.inspect(bind).get_indexes("analyses")
    }:
        op.drop_index("ix_analyses_production_configuration_id", table_name="analyses")
    # A fresh table-level constraint can be rebuilt only when its parent has
    # no rows, and therefore no dependent analysis/review history to cascade.
    if table_constraint:
        naming = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
        with op.batch_alter_table("analyses", naming_convention=naming) as batch:
            batch.drop_constraint("fk_analyses_production_configuration_id_production_promotions", type_="foreignkey")
            batch.drop_column("production_configuration_id")
    elif bind.dialect.name == "sqlite":
        op.execute("ALTER TABLE analyses DROP COLUMN production_configuration_id")
    else:
        op.drop_column("analyses", "production_configuration_id")
