"""The tool that reads a kept result is read_tool_output wherever a session
or a policy names it.

Revision ID: 202610141608
Revises: 202610141605
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141608"
down_revision = "202610141605"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610141608_the_read_tool_says_tool_output.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610141608_the_read_tool_says_tool_output.down.sql")
