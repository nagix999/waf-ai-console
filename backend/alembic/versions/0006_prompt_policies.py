"""Immutable local policy versions and pinned analysis prompt snapshots."""
from alembic import op
import sqlalchemy as sa


revision = "0006_prompt_policies"
down_revision = "0005_analysis_labels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    def create_table(name, *columns_and_constraints):
        if not sa.inspect(op.get_bind()).has_table(name):
            op.create_table(name, *columns_and_constraints)

    create_table(
        "prompt_policy_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("change_note", sa.String(1000), nullable=False),
        sa.Column("parent_version_id", sa.String(36), sa.ForeignKey("prompt_policy_versions.id", ondelete="RESTRICT")),
        sa.Column("policy_ciphertext", sa.Text(), nullable=False),
        sa.Column("encryption_key_version", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("version_number", name="uq_prompt_policy_version_number"),
        sa.CheckConstraint("version_number > 0", name="ck_prompt_policy_version_number"),
    )
    create_table(
        "prompt_policy_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("active_version_id", sa.String(36), sa.ForeignKey("prompt_policy_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_prompt_policy_state_singleton"),
        sa.CheckConstraint("revision >= 1", name="ck_prompt_policy_state_revision"),
    )
    # SQLite supports nullable ADD COLUMN REFERENCES without rebuilding the
    # analyses table and its existing cascade-linked history.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("analyses")}
    if "prompt_snapshot_ciphertext" not in columns:
        op.add_column("analyses", sa.Column("prompt_snapshot_ciphertext", sa.Text()))
    if "prompt_policy_version_id" in columns:
        return
    if op.get_bind().dialect.name == "sqlite":
        # Alembic cannot ALTER ADD CONSTRAINT on SQLite; nullable REFERENCES
        # needs to be part of ADD COLUMN itself.
        op.execute("ALTER TABLE analyses ADD COLUMN prompt_policy_version_id VARCHAR(36) REFERENCES prompt_policy_versions(id) ON DELETE RESTRICT")
    else:
        op.add_column("analyses", sa.Column("prompt_policy_version_id", sa.String(36)))
        op.create_foreign_key("fk_analyses_prompt_policy_version", "analyses", "prompt_policy_versions", ["prompt_policy_version_id"], ["id"], ondelete="RESTRICT")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM prompt_policy_versions")) or bind.scalar(sa.text("SELECT COUNT(*) FROM analyses WHERE prompt_policy_version_id IS NOT NULL OR prompt_snapshot_ciphertext IS NOT NULL")):
        raise RuntimeError("cannot_downgrade_with_prompt_policy_history")
    # A SQLite batch rebuild drops the old analyses table. Never do that with
    # records present: incoming cascade FKs could delete their audit history.
    if bind.scalar(sa.text("SELECT COUNT(*) FROM analyses")):
        raise RuntimeError("cannot_downgrade_prompt_schema_with_analysis_history")
    op.drop_table("prompt_policy_state")
    if bind.dialect.name == "sqlite":
        convention = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
        keys = sa.inspect(bind).get_foreign_keys("analyses")
        key = next(key for key in keys if key["constrained_columns"] == ["prompt_policy_version_id"])
        name = key["name"] or "fk_analyses_prompt_policy_version_id_prompt_policy_versions"
        with op.batch_alter_table("analyses", naming_convention=convention) as batch:
            batch.drop_constraint(name, type_="foreignkey")
            batch.drop_column("prompt_snapshot_ciphertext")
            batch.drop_column("prompt_policy_version_id")
    else:
        op.drop_constraint("fk_analyses_prompt_policy_version", "analyses", type_="foreignkey")
        op.drop_column("analyses", "prompt_snapshot_ciphertext")
        op.drop_column("analyses", "prompt_policy_version_id")
    op.drop_table("prompt_policy_versions")
