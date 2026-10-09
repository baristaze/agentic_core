"""The budgets, with their fence.

Revision ID: 202610091859
Revises: 202610091858
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610091859"
down_revision = "202610091858"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610091859_budgets.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610091859_budgets.down.sql")
