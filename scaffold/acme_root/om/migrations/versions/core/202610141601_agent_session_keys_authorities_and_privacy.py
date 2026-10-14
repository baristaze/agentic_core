"""A session's keys, authorities, and privacy say agent session, and its
waits and wakes too.

Revision ID: 202610141601
Revises: 202610121725
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141601"
down_revision = "202610121725"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610141601_agent_session_keys_authorities_and_privacy.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610141601_agent_session_keys_authorities_and_privacy.down.sql")
