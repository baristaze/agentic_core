"""A session's title is sealed: the sealed title beside the column the
release before reads, each session's title key on its privacy record, and
one title key per tenant that wraps them.

Revision ID: 202610250340
Revises: 202610150049
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610250340"
down_revision = "202610150049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610250340_a_sessions_title_is_sealed.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610250340_a_sessions_title_is_sealed.down.sql")
