"""A session's delete mark, its purge claim, the sweep's index, and the
purge login's reach on the sessions.

Revision ID: 202610020900
Revises: 202610020100
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610020900"
down_revision = "202610020100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020900_agent_sessions_deleted_and_purged.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020900_agent_sessions_deleted_and_purged.down.sql")
