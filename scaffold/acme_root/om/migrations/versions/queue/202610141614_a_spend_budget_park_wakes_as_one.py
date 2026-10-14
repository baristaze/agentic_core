"""A wake of a session parked on its spend budget names the spend_budget
reason and its spend hold.

Revision ID: 202610141614
Revises: 202610141611
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141614"
down_revision = "202610141611"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202610141614_a_spend_budget_park_wakes_as_one.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202610141614_a_spend_budget_park_wakes_as_one.down.sql")
