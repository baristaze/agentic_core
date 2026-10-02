"""The artifacts' records, with their fence, kept to SELECT and INSERT for
the serving logins.

Revision ID: 202610020700
Revises: 202610020401
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610020700"
down_revision = "202610020401"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610020700_artifacts.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610020700_artifacts.down.sql")
