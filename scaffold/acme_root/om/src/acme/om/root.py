"""The business-layer root: constructs every manager in dependency order and
hands back one frozen object with a field per manager."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from acme.infra.cache import CacheInterface, CacheScope
from acme.infra.root import InfraInterface
from acme.integrations.identity import IdentityProviderInterface
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.integrations.root import IntegrationsInterface
from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.agent_sessions.impl.manager import AgentSessionsManagerImpl, AgentSessionsOptions
from acme.om.agents import AgentsManagerInterface, ResultGateInterface
from acme.om.agents.impl.gate import ResultGateNullImpl
from acme.om.agents.impl.manager import AgentsManagerImpl, AgentsOptions
from acme.om.agents.types.kind import AgentKind, AgentKindCatalog
from acme.om.attribution import AttributionManagerInterface, PrincipalContext
from acme.om.attribution.impl.manager import (
    AttributionManagerImpl,
    AttributionOptions,
    no_principal_context,
)
from acme.om.base import utcnow
from acme.om.budgets import BudgetGateInterface, BudgetsManagerInterface
from acme.om.budgets.impl.gate import BudgetGateImpl, BudgetGateOptions
from acme.om.budgets.impl.manager import BudgetsManagerImpl, BudgetsOptions
from acme.om.budgets.impl.pricing import PricingTableImpl
from acme.om.budgets.pricing import PricingInterface
from acme.om.events import EventsManagerInterface
from acme.om.events.impl.manager import EventsManagerImpl, EventsOptions
from acme.om.idempotency import IdempotencyManagerInterface
from acme.om.idempotency.impl.manager import IdempotencyManagerImpl, IdempotencyOptions
from acme.om.media import MediaManagerInterface
from acme.om.media.impl.manager import MediaManagerImpl, MediaOptions
from acme.om.models.impl.manager import ModelsManagerImpl, ModelsOptions
from acme.om.models.impl.prices import ModelPricesNullImpl
from acme.om.models.impl.resolver import ModelResolverTableImpl, ResolverOptions
from acme.om.models.manager import ModelsManagerInterface
from acme.om.models.prices import ModelPricesInterface
from acme.om.orchestrations import OrchestrationsManagerInterface
from acme.om.orchestrations.impl.manager import OrchestrationsManagerImpl, OrchestrationsOptions
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.privacy import PrivacyManagerInterface
from acme.om.privacy.impl.keys import SessionKeysImpl
from acme.om.privacy.impl.manager import PrivacyManagerImpl, PrivacyOptions
from acme.om.privacy.impl.memory_only_steps import StepStorageShapeOnlyImpl
from acme.om.privacy.impl.routed_steps import StepStorageRoutedImpl
from acme.om.privacy.impl.sealed_steps import StepStorageSealedImpl
from acme.om.privacy.keys import SessionKeysInterface
from acme.om.steps import StepsManagerInterface
from acme.om.steps.impl.manager import StepsManagerImpl, StepsOptions
from acme.om.steps.storage import StepStorageInterface
from acme.om.steps.storage.impl.memory import StepStorageMemoryImpl
from acme.om.storage.root import StorageInterface
from acme.om.tenancy import TenancyManagerInterface, TenancyOperatorManagerInterface
from acme.om.tenancy.impl.credentials import TenancyCredentialsManagerImpl
from acme.om.tenancy.impl.manager import TenancyManagerImpl, TenancyOptions
from acme.om.tenancy.impl.members import TenancyMembersManagerImpl
from acme.om.tenancy.impl.operator import TenancyOperatorManagerImpl, TenancyOperatorOptions
from acme.om.tenancy.impl.org import TenancyOrgManagerImpl
from acme.om.tenancy.impl.sign_in import TenancySignInManagerImpl
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.work import WorkManagerInterface, WorkOperatorManagerInterface
from acme.om.work.impl.manager import WorkManagerImpl, WorkOptions
from acme.om.work.impl.operator import WorkOperatorManagerImpl


@dataclass(frozen=True)
class Managers:
    tenancy: TenancyManagerInterface
    tenancy_operator: TenancyOperatorManagerInterface
    work: WorkManagerInterface
    work_operator: WorkOperatorManagerInterface
    media: MediaManagerInterface
    idempotency: IdempotencyManagerInterface
    events: EventsManagerInterface
    outbox: OutboxRelayInterface
    orchestrations: OrchestrationsManagerInterface
    steps: StepsManagerInterface
    agent_sessions: AgentSessionsManagerInterface
    privacy: PrivacyManagerInterface
    budgets: BudgetsManagerInterface
    budget_gate: BudgetGateInterface
    pricing: PricingInterface
    models: ModelsManagerInterface
    attribution: AttributionManagerInterface
    agents: AgentsManagerInterface


async def purge_held(
    managers: Managers, org_id: UUID, session_id: UUID, tree_id: UUID | None
) -> None:
    """What attribution and the agents hold of a session the sweep purges:
    its authority, and its tree when it was the tree's last session."""
    await managers.attribution.purge_authority(org_id, session_id)
    if tree_id is not None:
        await managers.agents.purge_tree(org_id, tree_id)


def build_tenancy(
    storage: TenancyStorageInterface,
    relay: OutboxRelayInterface,
    cache: CacheInterface,
    options: TenancyOptions,
    clock: Callable[[], datetime] = utcnow,
    *,
    identity_provider: IdentityProviderInterface,
) -> TenancyManagerInterface:
    """The tenancy manager with its delegates, each built here and handed to
    it: a caller outside the namespace reaches a delegate through the
    manager, and no impl builds another. A delegate that calls a sibling
    takes it here, by its interface, and one that needs an operation of the
    manager takes that one operation as a callable. `clock` is the one the
    second factor's time step is read from."""
    sign_in = TenancySignInManagerImpl(
        storage, relay, options, clock, identity_provider=identity_provider
    )
    # The account's deletion writes a row under each place its person holds,
    # and a stage comes only from the manager's transition. The manager holds
    # this delegate, so that one edge is bound at call time.
    org = TenancyOrgManagerImpl(
        storage,
        relay,
        options,
        identity_provider=identity_provider,
        service_context=lambda rctx, org_id, user_id: tenancy.service_context(
            rctx, org_id, user_id
        ),
    )
    members = TenancyMembersManagerImpl(
        storage, relay, options, org=org, identity_provider=identity_provider
    )
    credentials = TenancyCredentialsManagerImpl(storage, relay, options)
    tenancy = TenancyManagerImpl(
        storage,
        relay,
        cache,
        options,
        sign_in=sign_in,
        org=org,
        members=members,
        credentials=credentials,
    )
    return tenancy


def private_history(
    storage: StorageInterface, keys: SessionKeysInterface, transient: StepStorageInterface
) -> StepStorageInterface:
    """The history as the engine reaches it: every session routed by its
    storage policy to content sealed at rest, to a shape at rest and its
    content in this process, or to nothing at rest (`transient`). Each impl
    is wired here, at boot; the steps manager sees one interface."""
    history = storage.get_step_storage()
    return StepStorageRoutedImpl(
        sealed=StepStorageSealedImpl(history, keys),
        shape_only=StepStorageShapeOnlyImpl(history),
        transient=transient,
        policies=storage.get_privacy_storage(),
    )


def build_managers(
    storage: StorageInterface,
    infra: InfraInterface,
    tenancy_options: TenancyOptions | None = None,
    operator_options: TenancyOperatorOptions | None = None,
    integrations: IntegrationsInterface | None = None,
    *,
    media_options: MediaOptions | None = None,
    idempotency_options: IdempotencyOptions | None = None,
    events_options: EventsOptions | None = None,
    work_options: WorkOptions | None = None,
    orchestrations_options: OrchestrationsOptions | None = None,
    steps_options: StepsOptions | None = None,
    agent_sessions_options: AgentSessionsOptions | None = None,
    budgets_options: BudgetsOptions | None = None,
    models_options: ModelsOptions | None = None,
    model_prices: ModelPricesInterface | None = None,
    agents_options: AgentsOptions | None = None,
    attribution_options: AttributionOptions | None = None,
    agent_kinds: tuple[AgentKind, ...] = (),
    principal_context: PrincipalContext | None = None,
    result_gate: ResultGateInterface | None = None,
) -> Managers:
    """`integrations` is the root of the hosted services the managers front:
    the identity provider, which the tenancy manager signs people in and
    invites them through. None is a process that signs nobody in, and every
    call that would reach the provider is refused as unavailable.

    The options after `integrations` are what the process that sweeps sets
    on the managers it purges through: each one's retention and batch. None
    keeps that manager's defaults.

    `model_prices` is what the resolver asks before it picks a model: the
    one source of prices. None wires the null, which prices nothing, so no
    model resolves until a source is wired.

    The last three are the adopter's for its agents: the agent kinds it
    declares, every version it still runs; the transition of its tenancy
    manager that answers for a principal's live permissions, which every
    tool call asks; and the gate a result passes. None wires the transition
    that answers for nobody, so every tool call is refused, and the null
    gate, which accepts a result and marks it unverified."""
    # The relay every core-role manager hands its outbox rows to. It reaches
    # the work manager through the root below, because a row of kind
    # `work.<kind>` is enqueued there: the work manager needs the tenancy
    # manager, which needs this relay, so that one edge is bound at call time
    # and the graph the root hands back is still whole.
    outbox = OutboxRelayImpl(
        storage.get_outbox_storage(),
        storage.get_event_storage(),
        infra.get_topics(),
        lambda: managers.work,
    )
    tenancy = build_tenancy(
        storage.get_tenancy_storage(),
        outbox,
        infra.get_cache(CacheScope.REALTIME_TICKET),
        tenancy_options or TenancyOptions(),
        identity_provider=(
            IdentityProviderAbsentImpl()
            if integrations is None
            else integrations.get_identity_provider()
        ),
    )
    events = EventsManagerImpl(
        storage.get_event_storage(), tenancy, events_options or EventsOptions()
    )
    work = WorkManagerImpl(
        storage.get_work_storage(),
        tenancy,
        events,
        infra.get_topics(),
        work_options or WorkOptions(),
    )
    media = MediaManagerImpl(
        storage.get_media_storage(),
        infra.get_buckets(),
        tenancy,
        outbox,
        media_options or MediaOptions(),
    )
    orchestrations = OrchestrationsManagerImpl(
        storage.get_orchestrations_storage(),
        tenancy,
        outbox,
        orchestrations_options or OrchestrationsOptions(),
    )
    # The history first: a session's status is read off its steps. What a
    # step says reaches it through the sealing layer, by the session's policy.
    session_keys = SessionKeysImpl(storage.get_privacy_storage(), infra.get_keys())
    steps = StepsManagerImpl(
        private_history(storage, session_keys, StepStorageMemoryImpl()),
        tenancy,
        steps_options or StepsOptions(),
    )
    agent_sessions = AgentSessionsManagerImpl(
        storage.get_agent_session_storage(),
        steps,
        tenancy,
        outbox,
        agent_sessions_options or AgentSessionsOptions(),
        # What attribution and the agents hold of a purged session goes with
        # it. Both are built below on this manager, so the edge is bound at
        # call time.
        purged=lambda org_id, session_id, tree_id: purge_held(
            managers, org_id, session_id, tree_id
        ),
    )
    privacy = PrivacyManagerImpl(
        storage.get_privacy_storage(),
        session_keys,
        steps,
        agent_sessions,
        tenancy,
        outbox,
        PrivacyOptions(),
    )
    # The gate reads the budgets of a call's scopes and holds on the ledger.
    budgets = BudgetsManagerImpl(
        storage.get_budget_storage(),
        storage.get_ledger_storage(),
        tenancy,
        outbox,
        budgets_options or BudgetsOptions(),
    )
    budget_gate = BudgetGateImpl(
        storage.get_budget_storage(), storage.get_ledger_storage(), BudgetGateOptions()
    )
    # A session's fills. The resolver refuses a model with no price row,
    # so a root that wires no prices resolves nothing.
    models = ModelsManagerImpl(
        storage.get_fill_set_storage(),
        steps,
        tenancy,
        ModelResolverTableImpl(model_prices or ModelPricesNullImpl(), ResolverOptions()),
        models_options or ModelsOptions(),
    )
    attribution = AttributionManagerImpl(
        storage.get_attribution_storage(),
        agent_sessions,
        steps,
        principal_context or no_principal_context,
        tenancy,
        outbox,
        attribution_options or AttributionOptions(),
    )
    agents = AgentsManagerImpl(
        storage.get_agent_storage(),
        agent_sessions,
        steps,
        attribution,
        result_gate or ResultGateNullImpl(),
        AgentKindCatalog(kinds=agent_kinds),
        tenancy,
        outbox,
        agents_options or AgentsOptions(),
    )
    idempotency = IdempotencyManagerImpl(
        storage.get_idempotency_storage(), idempotency_options or IdempotencyOptions()
    )
    tenancy_operator = TenancyOperatorManagerImpl(
        storage.get_tenancy_storage(),
        storage.get_event_storage(),
        outbox,
        operator_options or TenancyOperatorOptions(),
    )
    managers = Managers(
        tenancy=tenancy,
        tenancy_operator=tenancy_operator,
        work=work,
        work_operator=WorkOperatorManagerImpl(
            storage.get_work_storage(),
            storage.get_tenancy_storage(),
            storage.get_event_storage(),
            infra.get_topics(),
        ),
        media=media,
        idempotency=idempotency,
        events=events,
        outbox=outbox,
        orchestrations=orchestrations,
        steps=steps,
        agent_sessions=agent_sessions,
        privacy=privacy,
        budgets=budgets,
        budget_gate=budget_gate,
        # The one source of prices: the list table.
        pricing=PricingTableImpl(),
        models=models,
        attribution=attribution,
        agents=agents,
    )
    return managers
