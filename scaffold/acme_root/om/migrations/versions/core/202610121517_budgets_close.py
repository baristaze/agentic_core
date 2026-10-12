"""A budget closes, and the gate reads open budgets alone.

Revision ID: 202610121517
Revises: 202610091904
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610121517"
down_revision = "202610091904"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610121517_budgets_close.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610121517_budgets_close.down.sql")
