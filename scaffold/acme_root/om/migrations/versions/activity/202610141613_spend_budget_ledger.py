"""The ledger says spend: its holds, settlements, and tallies, the hold and
budget each names, and the park reason and hold a step names.

Revision ID: 202610141613
Revises: 202610141610
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141613"
down_revision = "202610141610"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610141613_spend_budget_ledger.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610141613_spend_budget_ledger.down.sql")
