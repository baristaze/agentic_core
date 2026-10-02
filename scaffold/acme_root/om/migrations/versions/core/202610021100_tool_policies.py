"""Each tenant's layer of tool policy, with its fence.

Revision ID: 202610021100
Revises: 202610020900
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610021100"
down_revision = "202610020900"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610021100_tool_policies.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610021100_tool_policies.down.sql")
