"""The history and its cursor rows, with their fences, and the history kept
to SELECT and INSERT for the serving logins.

Revision ID: 202610020101
Revises: 202609280001
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610020101"
down_revision = "202609280001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610020101_steps.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610020101_steps.down.sql")
