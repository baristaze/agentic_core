"""A wake of a session parked on its agent workspace names the
agent_workspace unlock.

Revision ID: 202610141611
Revises: 202610141603
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141611"
down_revision = "202610141603"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202610141611_a_lost_agent_workspace_unlocks_as_one.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202610141611_a_lost_agent_workspace_unlocks_as_one.down.sql")
