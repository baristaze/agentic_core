"""A session's kind, lineage, and cached attribution; the agent trees and
the session authorities, with their fences.

Revision ID: 202610020600
Revises: 202610020400
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610020600"
down_revision = "202610020400"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020600_agents_and_attribution.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610020600_agents_and_attribution.down.sql")
