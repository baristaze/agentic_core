"""The agent session as a waiter of the leases namespace: it waits while its
loop is open, and a grant, its request's end without a lease, and a
revocation each ask for a `LEASE_NOTICE` item that wakes a loop parked in
line. What happened is read by the run it wakes, and told to the model as
the engine's notice before its next model call."""

from collections.abc import Callable
from uuid import UUID

from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.agent_sessions.types.agent_session import SessionStatus
from acme.om.context import TenantContext
from acme.om.exceptions import NotFound
from acme.om.leases.hooks import WaiterInterface
from acme.om.leases.types.lease import Lease
from acme.om.leases.types.request import LeaseRequest
from acme.om.outbox.types.row import OutboxRow, outbox_row
from acme.om.work.types.work_item import LeaseNoticePayload, WorkKind, work_row_kind


class SessionWaiterImpl(WaiterInterface):
    """`sessions` answers the sessions manager, which the root builds after
    the leases namespace, so that edge is bound at call time."""

    def __init__(self, sessions: Callable[[], AgentSessionsManagerInterface]) -> None:
        self._sessions = sessions

    async def still_waits(self, ctx: TenantContext, waiter_id: UUID) -> bool:
        try:
            session = await self._sessions().get_session(ctx, waiter_id)
        except NotFound:
            return False
        return session.status is not SessionStatus.IDLE

    def wake_rows(self, ctx: TenantContext, waiter_id: UUID, lease: Lease) -> tuple[OutboxRow, ...]:
        return _notice(ctx, waiter_id, lease.request_id)

    def end_rows(
        self, ctx: TenantContext, waiter_id: UUID, request: LeaseRequest
    ) -> tuple[OutboxRow, ...]:
        return _notice(ctx, waiter_id, request.id)

    def revoke_rows(
        self, ctx: TenantContext, waiter_id: UUID, lease: Lease
    ) -> tuple[OutboxRow, ...]:
        return _notice(ctx, waiter_id, lease.request_id)


def _notice(ctx: TenantContext, session_id: UUID, request_id: UUID) -> tuple[OutboxRow, ...]:
    """A `LEASE_NOTICE` item naming the session and the request."""
    payload = LeaseNoticePayload(request_id=request_id)
    return (
        outbox_row(
            ctx,
            work_row_kind(WorkKind.LEASE_NOTICE),
            session_id,
            payload.model_dump(mode="json"),
        ),
    )
