"""The work that wakes a session says agent session.

Revision ID: 202610141603
Revises: 202609280003
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141603"
down_revision = "202609280003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202610141603_a_session_wakes_as_an_agent_session.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202610141603_a_session_wakes_as_an_agent_session.down.sql")
