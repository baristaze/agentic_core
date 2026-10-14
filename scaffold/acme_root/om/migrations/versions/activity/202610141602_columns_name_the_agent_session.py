"""Every column that names a session says agent session, and so does the
agent a step names.

Revision ID: 202610141602
Revises: 202610041626
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141602"
down_revision = "202610041626"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610141602_columns_name_the_agent_session.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610141602_columns_name_the_agent_session.down.sql")
