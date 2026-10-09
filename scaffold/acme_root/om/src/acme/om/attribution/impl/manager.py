from collections.abc import Callable, Mapping
from datetime import datetime
from uuid import UUID

from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.attribution.manager import AttributionManagerInterface, PrincipalContext
from acme.om.attribution.rules import (
    call_principal,
    inherited,
    needs_person,
    principal_of,
    speaker_after,
    spender_of,
)
from acme.om.attribution.storage import AttributionStorageInterface
from acme.om.attribution.types.authority import (
    AuthorityMode,
    CallAuthority,
    CallReach,
    RequestAttribution,
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
from acme.om.steps import StepsManagerInterface
from acme.om.steps.types.header import ModelRequestHeader
from acme.om.steps.types.step import Step
from acme.om.tenancy import TenancyManagerInterface

CREATED = "attribution.session_authority.created"
UPDATED = "attribution.session_authority.updated"


class AttributionOptions(Platform):
    purge_batch: int = 1000  # authorities one purge statement deletes at most
    read_batch: int = 200  # steps one read of a request's range holds


async def no_principal_context(
    rctx: RequestContext, org_id: UUID, principal: Principal
) -> TenantContext:
    """The transition that answers for nobody: every tool call is refused
    rather than run on an authority nobody asked about. A root wires it
    where no call may run on anyone's authority."""
    raise NotAuthorized(f"no transition answers for {principal.kind.value} {principal.id}")


def members_context(tenancy: TenancyManagerInterface) -> PrincipalContext:
    """The transition a root wires when its adopter hands it none: a person's
    live context as a member of the tenant, asked of the tenancy manager at
    every call, with the role their membership holds then, capped at the
    API key they spoke through, if any. A service principal is refused:
    the tenancy manager grants none."""

    async def live(rctx: RequestContext, org_id: UUID, principal: Principal) -> TenantContext:
        if principal.kind is not PrincipalKind.PERSON:
            raise NotAuthorized(f"no {principal.kind.value} principal is granted in the tenant")
        return await tenancy.delegated_context(rctx, org_id, principal.id, principal.key_id)

    return live


class AttributionManagerImpl(AttributionManagerInterface):
    def __init__(
        self,
        storage: AttributionStorageInterface,
        sessions: AgentSessionsManagerInterface,
        steps: StepsManagerInterface,
        principal_context: PrincipalContext,
        tenancy: TenancyManagerInterface,
        relay: OutboxRelayInterface,
        options: AttributionOptions,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._storage = storage
        self._sessions = sessions
        self._steps = steps
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
        if session.created_by != ctx.user_id:
            raise NotAuthorized(f"only the maker of agent session {session_id} opens its authority")
        came_from = session.parent_id or session.handed_off_from
        if came_from is None:
            # Its maker, as the context asking (the check above): through
            # the key it came on, if any, so a key's cap holds here too.
            principal = principal_of(ctx)
            spender = None
        else:
            source = await self._sessions.get_session_at_head(ctx, came_from)
            passed = await self._storage.read_authority(ctx.org_id, came_from)
            if passed is None:
                raise ValidationFailed(f"agent session {came_from} holds no authority to pass on")
            child = session.parent_id is not None
            principal, spender = inherited(
                passed, source.speaker, from_child=source.parent_id is not None, child=child
            )
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
        principal = principal_of(ctx)
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

    async def attribute_request(
        self, ctx: TenantContext, session_id: UUID, after_seq: int, delivered: Mapping[UUID, int]
    ) -> RequestAttribution:
        ctx.require(Permission.READ)
        if after_seq < 0:
            raise ValidationFailed(f"no model request at {after_seq}")
        authority = await self._authority(ctx, session_id)
        previous: Principal | None = None
        if after_seq > 0:
            found = await self._range(ctx, session_id, after_seq - 1, after_seq)
            latest = found[0] if found else None
            if latest is None or not isinstance(latest.header, ModelRequestHeader):
                raise ValidationFailed(f"step {after_seq} of {session_id} is no model request")
            previous = latest.header.speaker
        inputs = await self._delivered(ctx, session_id, delivered)
        speaker = speaker_after(previous, inputs)
        spender = spender_of(authority.spender, speaker)
        if spender is None:
            raise NoSpender(f"nobody can be named to pay for agent session {session_id}")
        return RequestAttribution(speaker=speaker, spender=spender)

    async def _delivered(
        self, ctx: TenantContext, session_id: UUID, wanted: Mapping[UUID, int]
    ) -> list[Step]:
        """The delivered inputs as the history holds them, in `seq` order:
        each read back at its place, never taken from the caller."""
        if not wanted:
            return []
        lowest, highest = min(wanted.values()), max(wanted.values())
        held = {
            step.id: step
            for step in await self._range(ctx, session_id, max(lowest - 1, 0), highest)
            if step.id in wanted and step.seq == wanted[step.id]
        }
        if len(held) != len(wanted):
            raise ValidationFailed(f"a delivered step is not in the history of {session_id}")
        return sorted(held.values(), key=lambda step: step.seq)

    async def is_marked(self, ctx: TenantContext, session_id: UUID) -> bool:
        ctx.require(Permission.READ)
        return (await self._at_head(ctx, session_id)).untrusted

    async def call_principal(self, ctx: TenantContext, session_id: UUID) -> Principal:
        ctx.require(Permission.READ)
        authority = await self._authority(ctx, session_id)
        session = await self._at_head(ctx, session_id)
        return call_principal(authority, session.speaker, child=session.parent_id is not None)

    async def authorize_call(
        self, ctx: TenantContext, session_id: UUID, reach: CallReach
    ) -> CallAuthority:
        ctx.require(Permission.WRITE)
        authority = await self._authority(ctx, session_id)
        session = await self._at_head(ctx, session_id)
        principal = call_principal(authority, session.speaker, child=session.parent_id is not None)
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
            needs_person=needs_person(marked=session.untrusted, reach=reach),
        )

    async def purge_authority(self, org_id: UUID, session_id: UUID) -> bool:
        return await self._storage.purge_authority(org_id, session_id)

    async def purge_tenant(self, ctx: TenantContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        return await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

    async def _range(
        self, ctx: TenantContext, session_id: UUID, after_seq: int, through_seq: int
    ) -> list[Step]:
        """The steps after `after_seq` through `through_seq`, page by page."""
        found: list[Step] = []
        while after_seq < through_seq:
            page = await self._steps.get_steps(
                ctx, session_id, after_seq, min(self._options.read_batch, through_seq - after_seq)
            )
            found.extend(step for step in page.items if step.seq <= through_seq)
            if not page.has_more or not page.items:
                break
            after_seq = page.items[-1].seq
        return found

    async def _authority(self, ctx: TenantContext, session_id: UUID) -> SessionAuthority:
        """A session with no authority runs no call and spends nothing."""
        authority = await self._storage.read_authority(ctx.org_id, session_id)
        if authority is None:
            raise NotFound(f"agent session {session_id} holds no authority")
        return authority

    async def _at_head(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        """The session with its speaker and mark as of the head of its
        history."""
        return await self._sessions.get_session_at_head(ctx, session_id)

    async def _relay_all(self, ctx: TenantContext, rows: tuple[OutboxRow, ...]) -> None:
        """The write has committed; a relay that fails is left to the sweep."""
        if rows:
            await self._relay.relay_all(ctx.org_id, rows)


def _refusal(mode: AuthorityMode, principal: Principal) -> AuthorityRevoked | PrincipalLapsed:
    who = f"{principal.kind.value} {principal.id}"
    if mode is AuthorityMode.STEADY:
        return PrincipalLapsed(f"{who} no longer holds this session's calls")
    return AuthorityRevoked(f"{who} no longer holds this call")
