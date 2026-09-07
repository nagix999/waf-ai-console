"""Independent Test profile role; existing Production selection is untouched."""
from alembic import op
import sqlalchemy as sa

revision = "0011_model_test_role"
down_revision = "0010_test_runs"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if "is_test" not in {column["name"] for column in sa.inspect(connection).get_columns("vllm_profiles")}:
        op.add_column("vllm_profiles", sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "uq_vllm_single_test" not in {index["name"] for index in sa.inspect(connection).get_indexes("vllm_profiles")}:
        op.create_index("uq_vllm_single_test", "vllm_profiles", ["is_test"], unique=True,
                        sqlite_where=sa.text("is_test = 1"), postgresql_where=sa.text("is_test = true"))


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM vllm_profiles WHERE is_test = true")):
        raise RuntimeError("cannot_downgrade_with_assigned_test_profile")
    op.drop_index("uq_vllm_single_test", table_name="vllm_profiles")
    # Avoid rebuilding a populated parent referenced by test/analysis history.
    # The unused false column is retained on SQLite and reused by upgrade.
    if connection.dialect.name != "sqlite":
        op.drop_column("vllm_profiles", "is_test")
