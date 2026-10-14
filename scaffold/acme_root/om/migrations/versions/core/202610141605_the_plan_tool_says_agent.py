"""The tool an agent writes its plan with is write_agent_plan wherever a
session or a policy names it.

Revision ID: 202610141605
Revises: 202610141601
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141605"
down_revision = "202610141601"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610141605_the_plan_tool_says_agent.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610141605_the_plan_tool_says_agent.down.sql")
