"""The purge login's reach on the history and its cursor rows.

Revision ID: 202610020901
Revises: 202610020401
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610020901"
down_revision = "202610020401"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610020901_steps_purged_by_their_login.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610020901_steps_purged_by_their_login.down.sql")
