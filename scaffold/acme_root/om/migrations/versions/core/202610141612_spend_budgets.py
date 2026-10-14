"""A budget is a spend budget: its table, its scope values, and the park
reason its gate writes.

Revision ID: 202610141612
Revises: 202610141609
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141612"
down_revision = "202610141609"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610141612_spend_budgets.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610141612_spend_budgets.down.sql")
