"""Each tenant's layer of tool policy, with its fence.

Revision ID: 202610020410
Revises: 202610020400
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610020410"
down_revision = "202610020400"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020410_tool_policies.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020410_tool_policies.down.sql")
