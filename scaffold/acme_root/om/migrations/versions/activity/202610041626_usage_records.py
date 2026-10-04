"""One content-free usage record per billed model call.

Revision ID: 202610041626
Revises: 202610021301
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610041626"
down_revision = "202610021301"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610041626_usage_records.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610041626_usage_records.down.sql")
