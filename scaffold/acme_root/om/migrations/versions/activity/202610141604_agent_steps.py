"""A session's steps and their cursors say agent step, and so does every
column that names one.

Revision ID: 202610141604
Revises: 202610141602
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141604"
down_revision = "202610141602"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610141604_agent_steps.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610141604_agent_steps.down.sql")
