"""Every table belongs to exactly one database role; this map is the single
source of truth for the schema, the pool, and the migration chain."""

from enum import StrEnum


class DatabaseRole(StrEnum):
    CORE = "core"
    ACTIVITY = "activity"
    QUEUE = "queue"
    ADMIN = "admin"


TABLE_ROLES: dict[str, DatabaseRole] = {
    "api_keys": DatabaseRole.CORE,
    "auth_sessions": DatabaseRole.CORE,
    "change_event_cursors": DatabaseRole.ACTIVITY,
    "change_events": DatabaseRole.ACTIVITY,
    "files": DatabaseRole.CORE,
    "idempotency_records": DatabaseRole.CORE,
    "identities": DatabaseRole.CORE,
    "invitations": DatabaseRole.CORE,
    "leasable_resources": DatabaseRole.CORE,
    "lease_requests": DatabaseRole.CORE,
    "leases": DatabaseRole.CORE,
    "memberships": DatabaseRole.CORE,
    "orchestrations": DatabaseRole.CORE,
    "orgs": DatabaseRole.CORE,
    "outbox_rows": DatabaseRole.CORE,
    "sign_in_delays": DatabaseRole.CORE,
    "socket_tickets": DatabaseRole.CORE,
    "system_sizes": DatabaseRole.ADMIN,
    "tenant_caps": DatabaseRole.QUEUE,
    "users": DatabaseRole.CORE,
    "work_items": DatabaseRole.QUEUE,
    # A copy adds its entries below this line.
    "agent_session_authorities": DatabaseRole.CORE,
    "agent_session_keys": DatabaseRole.CORE,
    "agent_session_privacy": DatabaseRole.CORE,
    "agent_sessions": DatabaseRole.CORE,
    "agent_step_cursors": DatabaseRole.ACTIVITY,
    "agent_steps": DatabaseRole.ACTIVITY,
    "agent_trees": DatabaseRole.CORE,
    "fill_sets": DatabaseRole.CORE,
    "spend_budget_holds": DatabaseRole.ACTIVITY,
    "spend_budget_settlements": DatabaseRole.ACTIVITY,
    "spend_budget_tallies": DatabaseRole.ACTIVITY,
    "spend_budgets": DatabaseRole.CORE,
    "tenant_title_keys": DatabaseRole.CORE,
    "tool_outputs": DatabaseRole.ACTIVITY,
    "tool_policies": DatabaseRole.CORE,
    "usage_records": DatabaseRole.ACTIVITY,
}

DROPPED_TABLE_ROLES: dict[str, DatabaseRole] = {
    "event_cursors": DatabaseRole.ACTIVITY,
    "events": DatabaseRole.ACTIVITY,
    "platform_sizes": DatabaseRole.ADMIN,
    "resources": DatabaseRole.CORE,
    "sessions": DatabaseRole.CORE,
    # A copy adds its entries below this line.
    "artifacts": DatabaseRole.ACTIVITY,
    "budget_holds": DatabaseRole.ACTIVITY,
    "budget_settlements": DatabaseRole.ACTIVITY,
    "budget_tallies": DatabaseRole.ACTIVITY,
    "budgets": DatabaseRole.CORE,
    "session_authorities": DatabaseRole.CORE,
    "session_keys": DatabaseRole.CORE,
    "session_privacy": DatabaseRole.CORE,
    "step_cursors": DatabaseRole.ACTIVITY,
    "steps": DatabaseRole.ACTIVITY,
}
"""Tables the migration chain made and later dropped or renamed, by the name
they had. No process reaches them, so `role_for` does not know them; only the
chain names them, and its role check reads this map beside the live one."""

APPEND_ONLY_TABLES: frozenset[str] = frozenset(
    {
        "agent_steps",
        "spend_budget_holds",
        "spend_budget_settlements",
        "usage_records",
        "tool_outputs",
    }
)
"""Tables whose rows are written once: the serving logins hold SELECT and
INSERT on them and never UPDATE or DELETE. The migration that creates one
takes the two back from the role's default privileges, and the login command
takes them back again after each grant it makes (ADR 1002, ADR 1006, ADR
1014)."""

PURGED_TABLES: frozenset[str] = frozenset(
    {
        "agent_sessions",
        "agent_steps",
        "agent_step_cursors",
        "tool_outputs",
        "agent_session_authorities",
        "agent_trees",
    }
)
"""The tables the purge login reaches: a session, its history and its
tool_outputs, its authority, and its tree, which go together when the session
is purged. It holds SELECT and DELETE on them and nothing
else, granted by the migrations that admit it and again by the login
command, and the tenant fence admits it within the tenant its transaction
names and never under the system scope (ADR 1010)."""


def role_for(table_name: str) -> DatabaseRole:
    try:
        return TABLE_ROLES[table_name]
    except KeyError:
        raise LookupError(
            f"table {table_name!r} has no database role; add it to TABLE_ROLES"
        ) from None
