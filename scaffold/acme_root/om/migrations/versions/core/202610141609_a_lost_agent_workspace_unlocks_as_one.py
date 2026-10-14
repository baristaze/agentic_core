"""A park on an agent's workspace is cleared by the agent_workspace unlock.

Revision ID: 202610141609
Revises: 202610141608
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141609"
down_revision = "202610141608"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610141609_a_lost_agent_workspace_unlocks_as_one.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610141609_a_lost_agent_workspace_unlocks_as_one.down.sql")
