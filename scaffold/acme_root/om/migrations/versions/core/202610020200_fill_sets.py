"""Every session's fill set, a row per version, with its fence, and kept to
SELECT and INSERT for the serving logins.

Revision ID: 202610020200
Revises: 202610020100
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610020200"
down_revision = "202610020100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020200_fill_sets.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020200_fill_sets.down.sql")
