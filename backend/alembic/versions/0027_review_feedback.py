"""Optional feedback ratings; preserve historical analyst decisions verbatim."""
from alembic import op
import sqlalchemy as sa

revision = "0027_review_feedback"
down_revision = "0026_test_run_stop"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"]: column for column in sa.inspect(bind).get_columns("reviews")}
    checks = {check["name"] for check in sa.inspect(bind).get_check_constraints("reviews")}
    # 0001 uses current metadata for fresh installs; older deployments need
    # SQLite's table rebuild to allow NULL without rewriting old decisions.
    if "rating" not in columns or not columns["decision"]["nullable"] or "ck_review_rating" not in checks:
        with op.batch_alter_table("reviews") as batch:
            if "rating" not in columns:
                batch.add_column(sa.Column("rating", sa.String(32), nullable=True))
            if not columns["decision"]["nullable"]:
                batch.alter_column("decision", existing_type=sa.String(32), nullable=True)
            if "ck_review_rating" not in checks:
                batch.create_check_constraint("ck_review_rating", "rating IS NULL OR rating IN ('like', 'dislike')")


def downgrade():
    bind = op.get_bind()
    # Older code cannot represent rating/comment-only feedback. Refuse data
    # loss rather than inventing an analyst verdict for these records.
    if bind.scalar(sa.text("SELECT COUNT(*) FROM reviews WHERE rating IS NOT NULL OR decision IS NULL")):
        raise RuntimeError("review_feedback_history_requires_current_schema")
    with op.batch_alter_table("reviews") as batch:
        batch.drop_constraint("ck_review_rating", type_="check")
        batch.drop_column("rating")
        batch.alter_column("decision", existing_type=sa.String(32), nullable=False)
