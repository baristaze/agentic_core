"""A tree's concurrency column is dropped: no statement of this release or of
the release before names it (ADR 0038).

Revision ID: 202611241945
Revises: 202610250340
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202611241945"
down_revision = "202610250340"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202611241945_a_trees_concurrency_column_is_dropped.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202611241945_a_trees_concurrency_column_is_dropped.down.sql")
