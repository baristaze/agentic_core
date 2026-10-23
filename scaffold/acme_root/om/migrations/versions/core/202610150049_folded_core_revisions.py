"""The engine's own tables of the core role, on the base's.

Revision ID: 202610150049
Revises: 202610221532
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610150049"
down_revision = "202610221532"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610150049_folded_core_revisions.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610150049_folded_core_revisions.down.sql")
