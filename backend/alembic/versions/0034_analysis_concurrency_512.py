"""Raise configurable analysis capacity without changing any saved limits."""
import re

from alembic import op
import sqlalchemy as sa

revision = "0034_analysis_concurrency_512"
down_revision = "0033_feedback_provenance"
branch_labels = None
depends_on = None

TABLE = "concurrency_configuration"
CONSTRAINT = "ck_concurrency_range"


def _condition(limit):
    return f"production >= 1 AND production <= {limit} AND test >= 1 AND test <= {limit}"


def _normalize(value):
    return re.sub(r'[\s()"`\[\]]', "", value or "").lower()


def _replace_limit(limit):
    bind = op.get_bind()
    checks = sa.inspect(bind).get_check_constraints(TABLE)
    current = next((item.get("sqltext") for item in checks if item.get("name") == CONSTRAINT), None)
    if _normalize(current) == _normalize(_condition(limit)):
        # 0001 creates current metadata on a fresh installation. Do not rebuild
        # its already-correct table or seed/reset the singleton's configuration.
        return
    if _normalize(current) not in {_normalize(_condition(32)), _normalize(_condition(512))}:
        raise RuntimeError("concurrency_range_constraint_unrecognized")
    # This configuration table has no dependent foreign keys. Rebuild only it,
    # copying the existing columns with INSERT SELECT (including raw JSON),
    # rather than updating/reseeding any values or touching analysis/slot rows.
    with op.batch_alter_table(TABLE, recreate="always") as batch:
        batch.drop_constraint(CONSTRAINT, type_="check")
        batch.create_check_constraint(CONSTRAINT, _condition(limit))


def upgrade():
    _replace_limit(512)


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text(
        "SELECT COUNT(*) FROM concurrency_configuration WHERE production > 32 OR test > 32"
    )):
        # Never silently clamp/reset a user's active configuration for rollback.
        raise RuntimeError("concurrency_limit_downgrade_requires_32_or_less")
    _replace_limit(32)
