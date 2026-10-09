"""A session's kind, lineage, and cached attribution; the agent trees and
the session authorities, with their fences.

Revision ID: 202610091902
Revises: 202610091901
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610091902"
down_revision = "202610091901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610091902_agents_and_attribution.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610091902_agents_and_attribution.down.sql")
