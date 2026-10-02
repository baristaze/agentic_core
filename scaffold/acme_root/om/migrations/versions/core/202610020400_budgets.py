"""The budgets, with their fence.

Revision ID: 202610020400
Revises: 202610020100
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610020400"
down_revision = "202610020100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020400_budgets.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020400_budgets.down.sql")
