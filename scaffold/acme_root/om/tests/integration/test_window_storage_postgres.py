"""The window storage contract over Postgres, and what only the database
holds: an artifact's record is written once, so the serving logins may read
and insert it and never rewrite or remove it."""

import pytest
from contracts.window_storage import WindowStorageContract
from sqlalchemy import text

from acme.om.storage.impl.pg_base import LoginSessions
from acme.om.storage.logins import RUNTIME_LOGIN, SYSTEM_LOGIN
from acme.om.storage.migrate import ensure_logins_at
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import MigrationSettings
from acme.om.windows.storage import WindowStorageInterface
from acme.om.windows.storage.impl.postgres import WindowStoragePostgresImpl

pytestmark = pytest.mark.integration


class TestWindowStoragePostgres(WindowStorageContract):
    @pytest.fixture
    def storage(self, pg_sessions: LoginSessions) -> WindowStorageInterface:
        return WindowStoragePostgresImpl(pg_sessions)


async def test_no_serving_login_rewrites_or_removes_an_artifact(
    pg_sessions: LoginSessions, migration_settings: MigrationSettings
) -> None:
    """A deploy makes the logins again before every migration, which grants
    DML on every table, so it runs here first: the records stay append-only
    after it."""
    settings = migration_settings
    await ensure_logins_at(settings.master_url(), settings.login_passwords())
    privilege = text("SELECT has_table_privilege(:login, 'activity.artifacts', :privilege)")
    held: set[tuple[str, str]] = set()
    async with pg_sessions[DatabaseRole.ACTIVITY]() as db:
        for login in (RUNTIME_LOGIN, SYSTEM_LOGIN):
            for kind in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                found = await db.execute(privilege, {"login": login, "privilege": kind})
                if found.scalar_one():
                    held.add((login, kind))
    assert held == {
        (RUNTIME_LOGIN, "SELECT"),
        (RUNTIME_LOGIN, "INSERT"),
        (SYSTEM_LOGIN, "SELECT"),
        (SYSTEM_LOGIN, "INSERT"),
    }
