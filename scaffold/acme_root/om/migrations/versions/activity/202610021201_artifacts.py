"""The artifacts' records, with their fence, kept to SELECT and INSERT for
the serving logins.

Revision ID: 202610021201
Revises: 202610020901
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610021201"
down_revision = "202610020901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610021201_artifacts.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610021201_artifacts.down.sql")
