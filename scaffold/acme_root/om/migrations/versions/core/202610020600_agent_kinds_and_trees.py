"""A session's kind, authority, lineage, and attribution; the agent trees,
with their fence.

Revision ID: 202610020600
Revises: 202610020100
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610020600"
down_revision = "202610020100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020600_agent_kinds_and_trees.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020600_agent_kinds_and_trees.down.sql")
