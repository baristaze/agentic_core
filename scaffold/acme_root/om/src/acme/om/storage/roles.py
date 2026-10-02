"""Every table belongs to exactly one database role; this map is the single
source of truth for the schema, the pool, and the migration chain."""

from enum import StrEnum


class DatabaseRole(StrEnum):
    CORE = "core"
    ACTIVITY = "activity"
    QUEUE = "queue"
    ADMIN = "admin"


TABLE_ROLES: dict[str, DatabaseRole] = {
    "orgs": DatabaseRole.CORE,
    "identities": DatabaseRole.CORE,
    "sign_in_delays": DatabaseRole.CORE,
    "users": DatabaseRole.CORE,
    "memberships": DatabaseRole.CORE,
    "sessions": DatabaseRole.CORE,
    "api_keys": DatabaseRole.CORE,
    "socket_tickets": DatabaseRole.CORE,
    "invitations": DatabaseRole.CORE,
    "work_items": DatabaseRole.QUEUE,
    "files": DatabaseRole.CORE,
    "idempotency_records": DatabaseRole.CORE,
    "events": DatabaseRole.ACTIVITY,
    "event_cursors": DatabaseRole.ACTIVITY,
    "outbox_rows": DatabaseRole.CORE,
    "orchestrations": DatabaseRole.CORE,
    "platform_sizes": DatabaseRole.ADMIN,
    "agent_sessions": DatabaseRole.CORE,
    "steps": DatabaseRole.ACTIVITY,
    "step_cursors": DatabaseRole.ACTIVITY,
}

APPEND_ONLY_TABLES: frozenset[str] = frozenset({"steps"})
"""Tables whose rows are written once: the serving logins hold SELECT and
INSERT on them and never UPDATE or DELETE. The migration that creates one
takes the two back from the role's default privileges, and the login command
takes them back again after each grant it makes (ADR 1002)."""

PURGED_TABLES: frozenset[str] = frozenset({"agent_sessions", "steps", "step_cursors"})
"""The tables the purge login reaches: a session and its history, which no
serving login may delete. It holds SELECT and DELETE on them and nothing
else, granted by the migrations that admit it and again by the login
command, and the tenant fence admits it within the tenant its transaction
names and never under the system scope (ADR 1010)."""

DROPPED_TABLE_ROLES: dict[str, DatabaseRole] = {}
"""Tables the migration chain made and later dropped. No process reaches
them, so `role_for` does not know them; only the chain names them, and its
role check reads this map beside the live one."""


def role_for(table_name: str) -> DatabaseRole:
    try:
        return TABLE_ROLES[table_name]
    except KeyError:
        raise LookupError(
            f"table {table_name!r} has no database role; add it to TABLE_ROLES"
        ) from None
