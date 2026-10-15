"""The engine's own revisions of the queue role: none changes its schema.

Revision ID: 202610141614
Revises: 202610141707
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141614"
down_revision = "202610141707"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202610141614_folded_queue_revisions.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202610141614_folded_queue_revisions.down.sql")
