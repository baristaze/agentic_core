"""The ledger's holds, settlements, and tallies, with their fences, and the
holds and settlements kept to SELECT and INSERT for the serving logins.

Revision ID: 202610020401
Revises: 202610020101
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610020401"
down_revision = "202610020101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610020401_budget_ledger.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610020401_budget_ledger.down.sql")
