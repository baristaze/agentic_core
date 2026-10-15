"""Each not-null constraint is named by its table and its column.

Revision ID: 202610150049
Revises: 202610141613
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610150049"
down_revision = "202610141613"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610150049_not_null_constraints_name_their_tables.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610150049_not_null_constraints_name_their_tables.down.sql")
