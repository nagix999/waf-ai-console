"""Add vLLM profiles and asynchronous verification runs."""

from alembic import op
from sqlalchemy import inspect

from app.models import VLLMProfile, VLLMTestRun

revision = "0002_vllm_profiles"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if VLLMProfile.__tablename__ not in tables:
        VLLMProfile.__table__.create(bind=bind)
    if VLLMTestRun.__tablename__ not in tables:
        VLLMTestRun.__table__.create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if VLLMTestRun.__tablename__ in tables:
        VLLMTestRun.__table__.drop(bind=bind)
    if VLLMProfile.__tablename__ in tables:
        VLLMProfile.__table__.drop(bind=bind)
