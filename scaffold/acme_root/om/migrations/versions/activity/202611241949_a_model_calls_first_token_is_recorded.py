"""A model call's time to its first token: a column of its usage record,
null on a row written before it.

Revision ID: 202611241949
Revises: 202610150049
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202611241949"
down_revision = "202610150049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202611241949_a_model_calls_first_token_is_recorded.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202611241949_a_model_calls_first_token_is_recorded.down.sql")
