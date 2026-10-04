"""A session holds whether it holds private data.

Revision ID: 202610041704
Revises: 202610021100
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610041704"
down_revision = "202610021100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610041704_agent_sessions_hold_private.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610041704_agent_sessions_hold_private.down.sql")
