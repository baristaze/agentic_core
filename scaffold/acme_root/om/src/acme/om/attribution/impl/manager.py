from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.attribution.manager import AttributionManagerInterface, PrincipalContext
from acme.om.attribution.rules import call_principal, inherited, needs_person, spender_of
from acme.om.attribution.storage import AttributionStorageInterface
from acme.om.attribution.types.authority import (
    AuthorityMode,
    CallAuthority,
    CallReach,
    SessionAuthority,
)
from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.base import Platform, utcnow
from acme.om.context import Permission, RequestContext, TenantContext
from acme.om.exceptions import (
    AuthorityRevoked,
    NoSpender,
    NotAuthenticated,
    NotAuthorized,
    NotFound,
    PrincipalLapsed,
    TenantMismatch,
    ValidationFailed,
)
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow, versioned_row
from acme.om.tenancy import TenancyManagerInterface

CREATED = "attribution.session_authority.created"
UPDATED = "attribution.session_authority.updated"


class AttributionOptions(Platform):
    purge_batch: int = 1000  # authorities one purge statement deletes at most


async def no_principal_context(
    rctx: RequestContext, org_id: UUID, principal: Principal
) -> TenantContext:
    """The transition a root wires when its adopter hands it none: it answers
    for nobody, so every tool call is refused rather than run on an
    authority nobody asked about."""
    raise NotAuthorized(f"no transition answers for {principal.kind.value} {principal.id}")


class AttributionManagerImpl(AttributionManagerInterface):
    def __init__(
        self,
        storage: AttributionStorageInterface,
        sessions: AgentSessionsManagerInterface,
        principal_context: PrincipalContext,
        tenancy: TenancyManagerInterface,
        relay: OutboxRelayInterface,
        options: AttributionOptions,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._storage = storage
        self._sessions = sessions
        self._principal_context = principal_context
        self._tenancy = tenancy
        self._relay = relay
        self._options = options
        self._clock = clock

    async def open_authority(
        self, ctx: TenantContext, session_id: UUID, mode: AuthorityMode
    ) -> SessionAuthority:
        ctx.require(Permission.WRITE)
        found = await self._storage.read_authority(ctx.org_id, session_id)
        if found is not None:
            return found
        session = await self._sessions.get_session(ctx, session_id)
        came_from = session.parent_id or session.handed_off_from
        if came_from is None:
            principal = Principal(kind=PrincipalKind.PERSON, id=session.created_by)
            spender = None
        else:
            source = await self._sessions.get_session_at_head(ctx, came_from)
            passed = await self._storage.read_authority(ctx.org_id, came_from)
            if passed is None:
                raise ValidationFailed(f"agent session {came_from} holds no authority to pass on")
            child = session.parent_id is not None
            principal, spender = inherited(passed, source.speaker, child=child)
        now = self._clock()
        authority = SessionAuthority(
            id=session_id,
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            mode=mode,
            principal=principal,
            spender=spender,
        )
        rows = (versioned_row(ctx, CREATED, authority.id, authority.version),)
        if not await self._storage.create_authority(ctx.org_id, authority, rows):
            # A retry under the same id answers the authority as stored.
            existing = await self._storage.read_authority(ctx.org_id, session_id)
            if existing is None:
                raise TenantMismatch(f"the authority of {session_id} is not in {ctx.org_id}")
            return existing
        await self._relay_all(ctx, rows)
        return authority

    async def get_authority(self, ctx: TenantContext, session_id: UUID) -> SessionAuthority:
        ctx.require(Permission.READ)
        return await self._authority(ctx, session_id)

    async def assign_principal(self, ctx: TenantContext, session_id: UUID) -> SessionAuthority:
        ctx.require(Permission.WRITE)
        session = await self._sessions.get_session(ctx, session_id)
        if session.parent_id is not None:
            raise ValidationFailed(f"agent session {session_id} runs under its parent's principal")
        authority = await self._authority(ctx, session_id)
        principal = Principal(kind=PrincipalKind.PERSON, id=ctx.user_id)
        if authority.principal == principal:
            return authority
        taken = SessionAuthority.model_validate(
            {
                **authority.model_dump(),
                "principal": principal,
                "version": authority.version + 1,
                "updated_at": self._clock(),
                "updated_by": ctx.user_id,
            }
        )
        rows = (versioned_row(ctx, UPDATED, taken.id, taken.version),)
        await self._storage.write_authority(ctx.org_id, taken, authority.version, rows)
        await self._relay_all(ctx, rows)
        return taken

    async def spender_for(self, ctx: TenantContext, session_id: UUID) -> Principal:
        ctx.require(Permission.READ)
        authority = await self._authority(ctx, session_id)
        speaker, _ = await self._at_head(ctx, session_id)
        spender = spender_of(authority.spender, speaker)
        if spender is None:
            raise NoSpender(f"nobody can be named to pay for agent session {session_id}")
        return spender

    async def is_marked(self, ctx: TenantContext, session_id: UUID) -> bool:
        ctx.require(Permission.READ)
        _, marked = await self._at_head(ctx, session_id)
        return marked

    async def call_principal(self, ctx: TenantContext, session_id: UUID) -> Principal:
        ctx.require(Permission.READ)
        authority = await self._authority(ctx, session_id)
        speaker, _ = await self._at_head(ctx, session_id)
        return call_principal(authority, speaker)

    async def authorize_call(
        self, ctx: TenantContext, session_id: UUID, reach: CallReach
    ) -> CallAuthority:
        ctx.require(Permission.READ)
        authority = await self._authority(ctx, session_id)
        speaker, marked = await self._at_head(ctx, session_id)
        principal = call_principal(authority, speaker)
        # Asked on every call: a permission taken away between two calls of
        # one loop stops the second (ADR 1007).
        try:
            live = await self._principal_context(ctx, ctx.org_id, principal)
        except (NotAuthorized, NotFound, NotAuthenticated) as refused:
            raise _refusal(authority.mode, principal) from refused
        if live.org_id != ctx.org_id or live.user_id != principal.id:
            # A transition that answers for someone else answers nothing.
            raise _refusal(authority.mode, principal)
        return CallAuthority(
            principal=principal,
            mode=authority.mode,
            context=live,
            needs_person=needs_person(marked=marked, reach=reach),
        )

    async def purge_tenant(self, ctx: TenantContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        return await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

    async def _authority(self, ctx: TenantContext, session_id: UUID) -> SessionAuthority:
        """A session with no authority runs no call and spends nothing."""
        authority = await self._storage.read_authority(ctx.org_id, session_id)
        if authority is None:
            raise NotFound(f"agent session {session_id} holds no authority")
        return authority

    async def _at_head(self, ctx: TenantContext, session_id: UUID) -> tuple[Principal | None, bool]:
        """The session's speaker and mark as of the head of its history."""
        session = await self._sessions.get_session_at_head(ctx, session_id)
        return session.speaker, session.untrusted

    async def _relay_all(self, ctx: TenantContext, rows: tuple[OutboxRow, ...]) -> None:
        """The write has committed; a relay that fails is left to the sweep."""
        if rows:
            await self._relay.relay_all(ctx.org_id, rows)


def _refusal(mode: AuthorityMode, principal: Principal) -> AuthorityRevoked | PrincipalLapsed:
    who = f"{principal.kind.value} {principal.id}"
    if mode is AuthorityMode.STEADY:
        return PrincipalLapsed(f"{who} no longer holds this session's calls")
    return AuthorityRevoked(f"{who} no longer holds this call")
