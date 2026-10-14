"""A result kept whole outside its step is a tool output: its table, the
key a step's header names it by, and the tool that reads it.

Revision ID: 202610141607
Revises: 202610141606
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141607"
down_revision = "202610141606"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610141607_tool_outputs.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610141607_tool_outputs.down.sql")
