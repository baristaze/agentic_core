"""A session holds whether it holds private data.

Revision ID: 202610091904
Revises: 202610091903
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610091904"
down_revision = "202610091903"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610091904_agent_sessions_hold_private.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610091904_agent_sessions_hold_private.down.sql")
