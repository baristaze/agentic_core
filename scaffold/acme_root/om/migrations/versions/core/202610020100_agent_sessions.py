"""The agent sessions, with their fence.

Revision ID: 202610020100
Revises: 202609280002
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610020100"
down_revision = "202609280002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020100_agent_sessions.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020100_agent_sessions.down.sql")
