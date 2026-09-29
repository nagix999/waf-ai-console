"""Measured list/runtime ordering index. No table or historical data rewrite."""
from alembic import op
import sqlalchemy as sa

revision = "0025_analysis_read_index"
down_revision = "0024_r5_contract"
branch_labels = None
depends_on = None


def upgrade():
    # 0001 creates current metadata on a fresh installation.
    if "ix_analyses_created_id" not in {row["name"] for row in sa.inspect(op.get_bind()).get_indexes("analyses")}:
        op.create_index("ix_analyses_created_id", "analyses", ["created_at", "id"])


def downgrade():
    op.drop_index("ix_analyses_created_id", table_name="analyses")
