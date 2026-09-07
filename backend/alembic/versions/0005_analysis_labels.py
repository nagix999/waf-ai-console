"""Separate append-only evaluation labels; old results remain untouched."""
from alembic import op
import sqlalchemy as sa
from app.models import AnalysisLabel

revision = "0005_analysis_labels"
down_revision = "0004_model_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    AnalysisLabel.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM analysis_labels")):
        raise RuntimeError("cannot_downgrade_with_evaluation_label_history")
    op.drop_table("analysis_labels")
