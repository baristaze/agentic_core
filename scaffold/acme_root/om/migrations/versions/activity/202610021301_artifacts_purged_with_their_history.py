"""The purge login's reach on the artifacts' records, and their index by
session.

Revision ID: 202610021301
Revises: 202610021201
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610021301"
down_revision = "202610021201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610021301_artifacts_purged_with_their_history.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610021301_artifacts_purged_with_their_history.down.sql")
