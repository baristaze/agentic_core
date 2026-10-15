"""The agent sessions' kinds of work and their kind of waiter, named here as
a copy names its own (`work.kinds`). The business root registers the work
kinds below the marker of `root.KINDS`, and the waiter below the marker of
the leases' waiters, so none is a line in a list the scaffold writes."""

from enum import StrEnum
from uuid import UUID

from acme.om.agent_steps.types.header import Park
from acme.om.agent_steps.types.header import ParkReason as LoopParkReason
from acme.om.base import OmBase
from acme.om.context import Permission
from acme.om.work.kinds import WorkKindSpec
from acme.om.work.types.work_item import ScheduledPayload


class AgentSessionWorkKind(StrEnum):
    """The names of the agent sessions' kinds of work."""

    WAKE_AGENT_SESSION = "WAKE_AGENT_SESSION"  # a parked session's retry time has come
    WAKE_AGENT_SESSIONS = "WAKE_AGENT_SESSIONS"  # the reason an org's sessions parked for is gone
    LOOP = "LOOP"  # a session's loop, for the session runner to run
    LEASE_NOTICE = "LEASE_NOTICE"  # a session's lease request answered, or its lease revoked


AGENT_SESSION_WAITER = "agent_session"
"""The kind of waiter an agent session waits in a line of the leases as: its
loop parks on the leasable resource while its ask waits."""

LOOP_LANE = "loop"
"""The lane a session's loop runs on: for minutes, on the session runner's
own lane, so a cap on the maintenance worker's lane counts none of a
tenant's loops."""


class WakeAgentSessionPayload(ScheduledPayload):
    """A parked session's retry time: the item waits in the queue until
    `not_before`, the park's retry time, and the item's target is the
    session. When it runs, a session still parked on exactly `park` is
    unlocked; one that moved on is left as it is."""

    park: Park


class WakeAgentSessionsPayload(OmBase):
    """The reason the org's sessions parked for is gone (a raised budget
    clears `spend_budget`); the item's target is the org. Every session
    parked for it is unlocked when the item runs, and its gates run again.
    With an `unlock`, only the sessions whose park names it are: a closed
    budget clears the parks that name its id, and no other."""

    reason: LoopParkReason
    unlock: str | None = None


class LoopPayload(OmBase):
    """A session's loop to run: an input woke the session, or an unlock let
    its park go. The item's target is the session; the run reads where the
    loop is from its history, so the item carries nothing else. Each time the
    session turns pending asks for one, and a run that finds nothing to do
    writes nothing."""


class LeaseNoticePayload(OmBase):
    """A lease request a session waits on was answered, by a grant or by its
    end without a lease, or the lease it was granted was revoked. The item's
    target is the session: one parked in line is unlocked, and its next run
    reads the request and tells the model. Any other session is left as it
    is: a running loop reads its requests before its next model call."""

    request_id: UUID


AGENT_SESSION_WORK_KINDS: tuple[WorkKindSpec, ...] = (
    # A park asks for the first and a raised budget for the second, each
    # relayed from its own commit; the handlers append a control and project
    # the status, which WRITE covers.
    WorkKindSpec(
        AgentSessionWorkKind.WAKE_AGENT_SESSION, WakeAgentSessionPayload, Permission.WRITE
    ),
    WorkKindSpec(
        AgentSessionWorkKind.WAKE_AGENT_SESSIONS, WakeAgentSessionsPayload, Permission.WRITE
    ),
    # A write that wakes a session asks for it, relayed from its own commit;
    # the run appends steps and projects the status, which WRITE covers, and
    # each tool call asks its principal's own permissions again.
    WorkKindSpec(AgentSessionWorkKind.LOOP, LoopPayload, Permission.WRITE, lane=LOOP_LANE),
    # A grant, a request's end, or a revocation asks for it, relayed from its
    # own commit; the handler unlocks a park, which WRITE covers.
    WorkKindSpec(AgentSessionWorkKind.LEASE_NOTICE, LeaseNoticePayload, Permission.WRITE),
)
"""The agent sessions' kinds, each with its payload, the permission that
asks for it, and its lane."""
