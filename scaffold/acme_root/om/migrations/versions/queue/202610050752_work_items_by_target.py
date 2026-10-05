"""The work items are indexed by their target.

Revision ID: 202610050752
Revises: 202609280002
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610050752"
down_revision = "202609280002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202610050752_work_items_by_target.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202610050752_work_items_by_target.down.sql")
