"""Doubles and helpers the manager suites share: just enough tenancy, a
context of a given role, the media manager over the memory storage, and
the model request a loop appends, with what attribution answers for it."""

from collections.abc import Sequence
from uuid import UUID

from acme.infra.impl.local import InfraLocalImpl
from acme.om.agent_steps.types.agent_step import AgentStep, AgentStepType
from acme.om.attribution.types.authority import RequestAttribution
from acme.om.base import new_id
from acme.om.context import (
    AppContext,
    AppType,
    CredentialKind,
    RequestContext,
    Role,
    TenantContext,
    build_context,
)
from acme.om.media.impl.manager import MediaManagerImpl, MediaOptions
from acme.om.media.storage.impl.memory import MediaStorageMemoryImpl
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.root import Managers
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tenancy.rules import permissions_of
from acme.om.tenancy.types.org import Org

from contracts.agent_step_storage import make_request
from contracts.factories import make_org, make_user

APP = AppContext(type=AppType.PORTAL, version="portal@test")


class Members(TenancyManagerInterface):
    """Just enough tenancy for the sweep's question: whether the tenant is
    past its retention. A partial double: only `tenant_expired` is reached,
    and any other method fails loudly as unimplemented, so the abstract set
    is cleared below."""

    def __init__(self) -> None:
        self.expired = False

    async def tenant_expired(self, ctx: TenantContext) -> bool:
        return self.expired


Members.__abstractmethods__ = frozenset()


def context(role: Role, org: Org | None = None) -> TenantContext:
    user = make_user(new_id())
    return build_context(
        RequestContext(request_id=new_id(), app=APP),
        user_id=user.id,
        org_id=(org or make_org()).id,
        role=role,
        permissions=permissions_of(role),
        credential_kind=CredentialKind.AUTH_SESSION_TOKEN,
    )


def media_of(
    outbox: OutboxStorageMemoryImpl,
    members: Members,
    relay: OutboxRelayImpl,
    infra: InfraLocalImpl,
) -> MediaManagerImpl:
    """The media manager over the memory storage, landing in the same outbox."""
    return MediaManagerImpl(
        MediaStorageMemoryImpl(outbox),
        infra.get_buckets(),
        members,
        relay,
        infra.get_flags(),
        MediaOptions(),
    )


async def next_attribution(
    managers: Managers, ctx: TenantContext, agent_session_id: UUID
) -> RequestAttribution:
    """What the session's next model request records, as the loop asks it:
    from the inputs since the latest model request, which the request
    delivers whole."""
    after, read = 0, 0
    inputs: dict[UUID, int] = {}
    while True:
        page = await managers.agent_steps.get_agent_steps(ctx, agent_session_id, read, 200)
        for step in page.items:
            if step.type is AgentStepType.MODEL_REQUEST:
                after, inputs = step.seq, {}
            elif step.type.is_input():
                inputs[step.id] = step.seq
        if not page.has_more or not page.items:
            break
        read = page.items[-1].seq
    return await managers.attribution.attribute_request(ctx, agent_session_id, after, inputs)


async def model_request(
    managers: Managers, ctx: TenantContext, agent_session_id: UUID, delivered: Sequence[AgentStep]
) -> AgentStep:
    """The loop's model request over the inputs it delivers, as a run
    appends it: it records the speaker and the spender attribution answers
    for them."""
    said = await next_attribution(managers, ctx, agent_session_id)
    epoch = await managers.agent_steps.begin_run(ctx, agent_session_id)
    loop = delivered[0].loop_id if delivered else new_id()
    request = make_request(
        agent_session_id,
        loop,
        tuple(step.id for step in delivered),
        spender=said.spender,
        speaker=said.speaker,
    )
    (stored,) = await managers.agent_steps.append_agent_steps(
        ctx, agent_session_id, epoch, [request]
    )
    return stored
