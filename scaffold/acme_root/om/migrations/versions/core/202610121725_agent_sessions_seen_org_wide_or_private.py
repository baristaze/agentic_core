"""A session is org-wide or private.

Revision ID: 202610121725
Revises: 202610121517
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610121725"
down_revision = "202610121517"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610121725_agent_sessions_seen_org_wide_or_private.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610121725_agent_sessions_seen_org_wide_or_private.down.sql")
