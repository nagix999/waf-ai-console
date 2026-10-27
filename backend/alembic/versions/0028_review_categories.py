"""Multi-select feedback; preserve historical decisions and ratings verbatim."""
from alembic import op
import sqlalchemy as sa

revision = "0028_review_categories"
down_revision = "0027_review_feedback"
branch_labels = None
depends_on = None

_CATEGORIES_CHECK = (
    "json_valid(feedback_categories) AND json_type(feedback_categories) = 'array' "
    "AND json_array_length(feedback_categories) <= 6"
)


def upgrade():
    bind = op.get_bind()
    # 0001 uses today's metadata on fresh installs. Existing v27 databases
    # only need an additive column; never translate legacy likes or verdicts.
    if "feedback_categories" not in {column["name"] for column in sa.inspect(bind).get_columns("reviews")}:
        op.add_column("reviews", sa.Column(
            "feedback_categories", sa.JSON(),
            sa.CheckConstraint(_CATEGORIES_CHECK, name="ck_review_feedback_categories"),
            nullable=False, server_default="[]",
        ))


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM reviews WHERE json_array_length(feedback_categories) > 0")):
        raise RuntimeError("review_categories_history_requires_current_schema")
    # Empty categories contain no extra information. Keep all other fields,
    # constraints, indexes and references when dropping the new column.
    with op.batch_alter_table("reviews") as batch:
        batch.drop_constraint("ck_review_feedback_categories", type_="check")
        batch.drop_column("feedback_categories")
