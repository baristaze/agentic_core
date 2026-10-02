"""Each session's privacy record and the versions of its key, with their
fences.

Revision ID: 202610020500
Revises: 202610020400
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610020500"
down_revision = "202610020400"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020500_session_privacy.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020500_session_privacy.down.sql")
