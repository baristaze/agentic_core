from uuid import UUID

from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.attribution.manager import AttributionManagerInterface, PrincipalContext
from acme.om.attribution.rules import call_principal, needs_person, spender_of
from acme.om.attribution.types.authority import (
    AuthorityMode,
    CallAuthority,
    CallReach,
)
from acme.om.attribution.types.principal import Principal
from acme.om.context import Permission, RequestContext, TenantContext
from acme.om.exceptions import (
    AuthorityRevoked,
    NoSpender,
    NotAuthenticated,
    NotAuthorized,
    NotFound,
    PrincipalLapsed,
)


async def no_principal_context(
    rctx: RequestContext, org_id: UUID, principal: Principal
) -> TenantContext:
    """The transition a root wires when its adopter hands it none: it answers
    for nobody, so every tool call is refused rather than run on an
    authority nobody asked about."""
    raise NotAuthorized(f"no transition answers for {principal.kind.value} {principal.id}")


class AttributionManagerImpl(AttributionManagerInterface):
    def __init__(
        self, sessions: AgentSessionsManagerInterface, principal_context: PrincipalContext
    ) -> None:
        self._sessions = sessions
        self._principal_context = principal_context

    async def spender_for(self, ctx: TenantContext, session_id: UUID) -> Principal:
        ctx.require(Permission.READ)
        session, speaker, _ = await self._read(ctx, session_id)
        spender = spender_of(session.spender, speaker)
        if spender is None:
            raise NoSpender(f"nobody can be named to pay for agent session {session_id}")
        return spender

    async def is_marked(self, ctx: TenantContext, session_id: UUID) -> bool:
        ctx.require(Permission.READ)
        _, _, marked = await self._read(ctx, session_id)
        return marked

    async def call_principal(self, ctx: TenantContext, session_id: UUID) -> Principal:
        ctx.require(Permission.READ)
        session, speaker, _ = await self._read(ctx, session_id)
        return call_principal(session.authority, speaker)

    async def authorize_call(
        self, ctx: TenantContext, session_id: UUID, reach: CallReach
    ) -> CallAuthority:
        ctx.require(Permission.READ)
        session, speaker, marked = await self._read(ctx, session_id)
        mode = session.authority.mode
        principal = call_principal(session.authority, speaker)
        # Asked on every call: a permission taken away between two calls of
        # one loop stops the second (ADR 1007).
        try:
            live = await self._principal_context(ctx, ctx.org_id, principal)
        except (NotAuthorized, NotFound, NotAuthenticated) as refused:
            raise _refusal(mode, principal) from refused
        if live.org_id != ctx.org_id or live.user_id != principal.id:
            # A transition that answers for someone else answers nothing.
            raise _refusal(mode, principal)
        return CallAuthority(
            principal=principal,
            mode=mode,
            context=live,
            needs_person=needs_person(marked=marked, reach=reach),
        )

    async def _read(
        self, ctx: TenantContext, session_id: UUID
    ) -> tuple[AgentSession, Principal | None, bool]:
        """The session, and its speaker and mark as of the head of its
        history."""
        session = await self._sessions.get_session_at_head(ctx, session_id)
        return session, session.speaker, session.untrusted


def _refusal(mode: AuthorityMode, principal: Principal) -> AuthorityRevoked | PrincipalLapsed:
    who = f"{principal.kind.value} {principal.id}"
    if mode is AuthorityMode.STEADY:
        return PrincipalLapsed(f"{who} no longer holds this session's calls")
    return AuthorityRevoked(f"{who} no longer holds this call")
