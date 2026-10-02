"""The attribution swimlane: who stands behind each step of a session, who
pays for its next model call, whose authority its next tool call runs
under, and whether a convinced model needs a person before it acts
outward.

It keeps no record of its own. Every answer is read off the session (its
authority, the spender its spawn passed it, and the speaker and the mark
it caches) and the steps after its cache, so an answer is never older
than the history."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from uuid import UUID

from acme.om.attribution.types.authority import CallAuthority, CallReach
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
    async def spender_for(self, ctx: TenantContext, session_id: UUID) -> Principal:
        """Who pays for the session's next model call: the principal behind
        the latest principal-authored input in its history, else the spender
        its spawn passed it. An input from an agent, the engine, or an
        external event never becomes the payer. `NoSpender` when nobody can
        be named, and then nothing is spent."""
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
        session's fixed principal, or a delegated session's latest
        speaker."""
        ...

    @abstractmethod
    async def authorize_call(
        self, ctx: TenantContext, session_id: UUID, reach: CallReach
    ) -> CallAuthority:
        """Asked for every tool call, before it runs. The call's principal
        is asked of the adopter's transition each time, never once per loop,
        and the call runs under the context it answers with. A delegated
        call whose principal no longer holds is `AuthorityRevoked`, and is
        denied; a steady one is `PrincipalLapsed`, and parks until a person
        takes the session over. `needs_person` is the rule of two."""
        ...
