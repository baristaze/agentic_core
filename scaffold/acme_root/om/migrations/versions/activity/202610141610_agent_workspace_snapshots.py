"""A snapshot a step names keeps its agent workspace's id, and a park on
one is cleared by the agent_workspace unlock.

Revision ID: 202610141610
Revises: 202610141607
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141610"
down_revision = "202610141607"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610141610_agent_workspace_snapshots.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610141610_agent_workspace_snapshots.down.sql")
