"""The handlers that wake parked agent sessions: one session whose park's
retry time came, every session of an org whose reason to wait is gone, and
one session in line whose request was answered or whose lease was revoked.

A park is "not now", never "failed": a loop that parks on a provider or a
budget resumes by itself once its unlock happens. Each handler writes the
engine's `unlock` control and leaves the session pending for a run, whose
gates run again before its next call. Both are idempotent: a session that is
no longer parked as the item says is left as it is."""

import logging
from typing import ClassVar

from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.agent_sessions.types.agent_session import SessionStatus
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import NotFound
from acme.om.work.types.handler import WorkHandlerInterface
from acme.om.work.types.work_item import (
    LeaseNoticePayload,
    WakeSessionPayload,
    WakeSessionsPayload,
    WorkItem,
)

log = logging.getLogger(__name__)


class WakeSessionHandlerImpl(WorkHandlerInterface):
    """A park's retry time came: the session the item targets is unlocked
    when it still waits on the park the item names."""

    REQUIRES: ClassVar[tuple[Permission, ...]] = (Permission.WRITE,)
    """`wake_session` writes."""

    def __init__(self, sessions: AgentSessionsManagerInterface) -> None:
        self._sessions = sessions

    async def handle(self, ctx: TenantContext, item: WorkItem) -> None:
        park = WakeSessionPayload.model_validate(dict(item.payload)).park
        try:
            session = await self._sessions.wake_session(ctx, item.target_id, park)
        except NotFound:
            log.info("session %s is gone; wake %s has nothing to do", item.target_id, item.id)
            return
        woken = session.status is not SessionStatus.PARKED or session.park != park
        log.info(
            "session %s in org %s at its %s retry time: %s",
            session.id,
            ctx.org_id,
            park.reason.value,
            session.status.value if woken else "still parked",
        )


class WakeSessionsHandlerImpl(WorkHandlerInterface):
    """The reason the org's sessions parked for is gone: every one of them is
    unlocked, and its gates run again when it resumes."""

    REQUIRES: ClassVar[tuple[Permission, ...]] = (Permission.WRITE,)
    """`wake_parked` writes."""

    def __init__(self, sessions: AgentSessionsManagerInterface) -> None:
        self._sessions = sessions

    async def handle(self, ctx: TenantContext, item: WorkItem) -> None:
        reason = WakeSessionsPayload.model_validate(dict(item.payload)).reason
        woken = await self._sessions.wake_parked(ctx, reason)
        log.info("woke %d sessions parked for %s in org %s", woken, reason.value, ctx.org_id)


class LeaseNoticeHandlerImpl(WorkHandlerInterface):
    """A request the session the item targets waits on was answered, or its
    lease was revoked: a loop parked in line is unlocked, and its run reads
    the request and tells the model before any model call. A session that
    runs, or parks for another reason, is left as it is: its run reads its
    requests before its next model call."""

    REQUIRES: ClassVar[tuple[Permission, ...]] = (Permission.WRITE,)
    """`wake_session` writes."""

    def __init__(self, sessions: AgentSessionsManagerInterface) -> None:
        self._sessions = sessions

    async def handle(self, ctx: TenantContext, item: WorkItem) -> None:
        request_id = LeaseNoticePayload.model_validate(dict(item.payload)).request_id
        try:
            session = await self._sessions.get_session(ctx, item.target_id)
        except NotFound:
            log.info("session %s is gone; notice %s has nothing to do", item.target_id, item.id)
            return
        park = session.park
        if session.status is not SessionStatus.PARKED or park is None or park.line is None:
            log.info(
                "session %s in org %s is %s; request %s is read by its run",
                session.id,
                ctx.org_id,
                session.status.value,
                request_id,
            )
            return
        woken = await self._sessions.wake_session(ctx, session.id, park)
        log.info(
            "session %s in org %s in line, request %s answered: %s",
            session.id,
            ctx.org_id,
            request_id,
            woken.status.value,
        )
