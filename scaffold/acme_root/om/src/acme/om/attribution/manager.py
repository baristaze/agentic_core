"""The attribution swimlane: who stands behind each step of a session, who
pays for its next model call, whose authority its next tool call runs
under, and whether a convinced model needs a person before it acts
outward.

It keeps one record per session, its authority: the mode, the principal,
and the spender a spawn passed it. The rest is read off the session (the
speaker and the mark it caches with its status) and the steps after that
cache, so an answer is never older than the history. A session with no
authority runs no tool call and spends nothing."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from uuid import UUID

from acme.om.attribution.types.authority import (
    AuthorityMode,
    CallAuthority,
    CallReach,
    RequestAttribution,
    SessionAuthority,
)
from acme.om.attribution.types.principal import Principal
from acme.om.context import RequestContext, TenantContext

PrincipalContext = Callable[[RequestContext, UUID, Principal], Awaitable[TenantContext]]
"""The adopter's transition, one operation of its tenancy manager: the live
context of a principal in the tenant `org_id`, carrying the permissions the
principal holds now, never the system's. It refuses (`NotAuthorized`,
`NotFound`, `NotAuthenticated`) when the principal holds no place in the
tenant any more."""


class AttributionManagerInterface(ABC):
    @abstractmethod
    async def open_authority(
        self, ctx: TenantContext, session_id: UUID, mode: AuthorityMode
    ) -> SessionAuthority:
        """The session's authority, made once, right after the session. The
        caller names the mode, its kind's, and nothing else: a root runs
        under the person who made it; a session made from another runs
        under the principal that session's calls run under, carries its
        mark, and, for a child, pays as its parent pays. Only the session's
        maker opens it (`NotAuthorized`), and a session to come from that
        holds no authority is `ValidationFailed`. Asked again, it answers
        the authority as stored."""
        ...

    @abstractmethod
    async def get_authority(self, ctx: TenantContext, session_id: UUID) -> SessionAuthority:
        """A session's authority; `NotFound` for one that holds none, and
        for another tenant's."""
        ...

    @abstractmethod
    async def assign_principal(self, ctx: TenantContext, session_id: UUID) -> SessionAuthority:
        """The caller takes a session over: its tool calls run under them
        from here. A steady session whose principal no longer holds waits
        until a person does this. A child's principal is its parent's, and
        a child is never taken over (`ValidationFailed`)."""
        ...

    @abstractmethod
    async def attribute_request(
        self, ctx: TenantContext, session_id: UUID, after_seq: int, through_seq: int
    ) -> RequestAttribution:
        """What the session's next model request records, read from the
        history: `after_seq` is the seq of its latest model request (0
        before its first), and `through_seq` the last step the new request
        delivers. The speaker is the principal behind the latest
        principal-authored input between the two, else the one that request
        recorded; the spender is that speaker, else the spender its spawn
        passed it. An input from an agent, the engine, or an external event
        never becomes the payer. `NoSpender` when nobody can be named, and
        then nothing is spent. A range whose start is no model request, or
        that holds one, is `ValidationFailed`."""
        ...

    @abstractmethod
    async def is_marked(self, ctx: TenantContext, session_id: UUID) -> bool:
        """Whether the session carries the untrusted mark: set by the first
        data in its history or passed from the session it came from, and
        never cleared."""
        ...

    @abstractmethod
    async def call_principal(self, ctx: TenantContext, session_id: UUID) -> Principal:
        """Whose authority the session's next tool call runs under: a steady
        session's fixed principal, or the speaker a delegated session's
        latest model request recorded."""
        ...

    @abstractmethod
    async def authorize_call(
        self, ctx: TenantContext, session_id: UUID, reach: CallReach
    ) -> CallAuthority:
        """Asked for every tool call, before it runs, and before the next
        model request: a delegated call runs under the speaker the request
        that led to it recorded, never a message that landed after it. The
        call's principal is asked of the adopter's transition each time,
        never once per loop,
        and the call runs under the context it answers with. A delegated
        call whose principal no longer holds is `AuthorityRevoked`, and is
        denied; a steady one is `PrincipalLapsed`, and waits until a person
        takes the session over. `needs_person` is the rule of two."""
        ...

    @abstractmethod
    async def purge_authority(self, org_id: UUID, session_id: UUID) -> bool:
        """Platform-internal: the authority of a session the sweep has
        claimed for its purge goes before its row, under the purge login, in
        the tenant named; for no principal. False when none was left."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: TenantContext) -> int:
        """The sweep, for one tenant past its own retention: every
        authority, a batch at most a call. Any other tenant returns 0 and
        reads nothing."""
        ...
