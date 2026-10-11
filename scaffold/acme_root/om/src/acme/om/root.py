"""The business-layer root: constructs every manager in dependency order and
hands back one frozen object with a field per manager."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from acme.infra.base import QuietNull
from acme.infra.cache import CacheInterface, CacheScope
from acme.infra.root import InfraInterface
from acme.infra.transports import SecretVia
from acme.integrations.identity import IdentityProviderInterface
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.integrations.model_providers.registry import absent_model_providers
from acme.integrations.root import IntegrationsInterface
from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.agent_sessions.impl.manager import AgentSessionsManagerImpl, AgentSessionsOptions
from acme.om.agents import AgentsManagerInterface, ResultGateInterface
from acme.om.agents.impl.gate import ResultGateNullImpl
from acme.om.agents.impl.loop import LoopManagerImpl, LoopOptions
from acme.om.agents.impl.manager import AgentsManagerImpl, AgentsOptions
from acme.om.agents.impl.sink import StreamSinkNullImpl
from acme.om.agents.impl.waiter import SessionWaiterImpl
from acme.om.agents.loop import LoopManagerInterface
from acme.om.agents.sink import StreamSinkInterface
from acme.om.agents.types.kind import AgentKind, AgentKindCatalog
from acme.om.attribution import AttributionManagerInterface, PrincipalContext
from acme.om.attribution.impl.manager import (
    AttributionManagerImpl,
    AttributionOptions,
    members_context,
)
from acme.om.base import utcnow
from acme.om.budgets import (
    BudgetGateInterface,
    BudgetsManagerInterface,
    BudgetsOperatorManagerInterface,
)
from acme.om.budgets.impl.gate import BudgetGateImpl, BudgetGateOptions
from acme.om.budgets.impl.manager import BudgetsManagerImpl, BudgetsOptions
from acme.om.budgets.impl.operator import BudgetsOperatorManagerImpl, BudgetsOperatorOptions
from acme.om.budgets.impl.pricing import PricingTableImpl
from acme.om.budgets.pricing import PricingInterface
from acme.om.events import EventsManagerInterface
from acme.om.events.impl.manager import EventsManagerImpl, EventsOptions
from acme.om.exceptions import UnsafeConfiguration
from acme.om.idempotency import IdempotencyManagerInterface
from acme.om.idempotency.impl.manager import IdempotencyManagerImpl, IdempotencyOptions
from acme.om.leases import LeasesManagerInterface
from acme.om.leases.impl.kinds import NoopResourceKindImpl, OrchestrationWaiterImpl
from acme.om.leases.impl.manager import LeasesManagerImpl, LeasesOptions
from acme.om.leases.types.request import WaiterKind
from acme.om.leases.types.resource import ResourceKind
from acme.om.media import MediaManagerInterface
from acme.om.media.impl.manager import MediaManagerImpl, MediaOptions
from acme.om.models.impl.credentials import CallCredentialsPlatformImpl
from acme.om.models.impl.manager import ModelsManagerImpl, ModelsOptions
from acme.om.models.impl.prices import ModelPricesFromPricingImpl
from acme.om.models.impl.resolver import ModelResolverTableImpl, ResolverOptions
from acme.om.models.layer import ModelsLayer
from acme.om.models.manager import ModelsManagerInterface
from acme.om.models.prices import ModelPricesInterface
from acme.om.orchestrations import OrchestrationsManagerInterface
from acme.om.orchestrations.impl.manager import OrchestrationsManagerImpl, OrchestrationsOptions
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.privacy import PrivacyManagerInterface
from acme.om.privacy.impl.artifacts import ArtifactSealKeysImpl
from acme.om.privacy.impl.keys import SessionKeysImpl
from acme.om.privacy.impl.manager import PrivacyManagerImpl, PrivacyOptions
from acme.om.privacy.impl.memory_only_steps import StepStorageShapeOnlyImpl
from acme.om.privacy.impl.records import RecordSealKeysImpl
from acme.om.privacy.impl.routed_steps import StepStorageRoutedImpl
from acme.om.privacy.impl.sealed_steps import StepStorageSealedImpl
from acme.om.privacy.impl.snapshots import SnapshotSealKeysImpl
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
from acme.om.tools import ToolRegistry, ToolsManagerInterface
from acme.om.tools.attachments import AttachmentReaderInterface
from acme.om.tools.impl.attachments import AttachmentReaderNullImpl
from acme.om.tools.impl.bases import WorkspaceBases
from acme.om.tools.impl.manager import ToolsManagerImpl, ToolsOptions
from acme.om.tools.impl.snapshots import SnapshotStore
from acme.om.tools.native.ask_person import AskPersonToolImpl
from acme.om.tools.native.read_artifact import ReadArtifactToolImpl
from acme.om.tools.native.read_attachment import ReadAttachmentToolImpl
from acme.om.tools.native.spawn_sub_agent import SpawnSubAgentToolImpl
from acme.om.tools.native.wait_for_sub_agents import WaitForSubAgentsToolImpl
from acme.om.tools.native.write_plan import WritePlanToolImpl
from acme.om.tools.seal import RecordSealInterface, SnapshotSealInterface
from acme.om.tools.tool import ToolInterface
from acme.om.windows import WindowsManagerInterface
from acme.om.windows.gate import CallGateInterface
from acme.om.windows.hashes import PromptHashInterface
from acme.om.windows.impl.gate import CallGateBudgetImpl
from acme.om.windows.impl.hashes import PromptHashPrivacyImpl
from acme.om.windows.impl.manager import WindowsManagerImpl, WindowsOptions
from acme.om.windows.seal import ArtifactSealInterface
from acme.om.windows.types.policy import CompactionPolicy
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
    leases: LeasesManagerInterface
    steps: StepsManagerInterface
    agent_sessions: AgentSessionsManagerInterface
    privacy: PrivacyManagerInterface
    budgets: BudgetsManagerInterface
    budgets_operator: BudgetsOperatorManagerInterface
    budget_gate: BudgetGateInterface
    pricing: PricingInterface
    models: ModelsManagerInterface
    windows: WindowsManagerInterface
    attribution: AttributionManagerInterface
    agents: AgentsManagerInterface
    tools: ToolsManagerInterface
    loop: LoopManagerInterface


LOCAL = "local"
"""The one environment a root accepts a quiet null budget gate or ledger in."""


def refuse_quiet_spend(environment: str, *capabilities: object) -> None:
    """Outside `local`, a root refuses a quiet null budget gate or ledger at
    boot: every model call would pass a gate that holds nothing, and spend
    outside every budget. A loud null is no such risk: it refuses each call
    itself."""
    if environment == LOCAL:
        return
    for capability in capabilities:
        if isinstance(capability, QuietNull):
            raise UnsafeConfiguration(
                f"{type(capability).__name__} holds nothing, and is refused "
                f"when the environment is {environment}"
            )


async def purge_held(
    managers: Managers, org_id: UUID, session_id: UUID, tree_id: UUID | None
) -> None:
    """What the windows, the tools, attribution, and the agents hold of a
    session the sweep purges: its artifacts, and its workspace with its
    transport's records, which go with its history, its authority, and its
    tree when it was the tree's last session."""
    await managers.windows.purge_artifacts(org_id, session_id)
    await managers.tools.purge_workspace(org_id, session_id)
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
    leases_options: LeasesOptions | None = None,
    steps_options: StepsOptions | None = None,
    agent_sessions_options: AgentSessionsOptions | None = None,
    budgets_options: BudgetsOptions | None = None,
    models_options: ModelsOptions | None = None,
    model_prices: ModelPricesInterface | None = None,
    models_layer: ModelsLayer | None = None,
    call_gate: CallGateInterface | None = None,
    prompt_hash: PromptHashInterface | None = None,
    artifact_seal: ArtifactSealInterface | None = None,
    record_seal: RecordSealInterface | None = None,
    snapshot_seal: SnapshotSealInterface | None = None,
    compaction_policy: CompactionPolicy | None = None,
    agents_options: AgentsOptions | None = None,
    attribution_options: AttributionOptions | None = None,
    agent_kinds: tuple[AgentKind, ...] = (),
    principal_context: PrincipalContext | None = None,
    result_gate: ResultGateInterface | None = None,
    tools_options: ToolsOptions | None = None,
    budget_gate: BudgetGateInterface | None = None,
    stream_sink: StreamSinkInterface | None = None,
    tool_catalog: tuple[ToolInterface, ...] = (),
    attachment_reader: AttachmentReaderInterface | None = None,
    domain_classes: tuple[str, ...] = (),
    loop_options: LoopOptions | None = None,
    environment: str = LOCAL,
) -> Managers:
    """`integrations` is the root of the hosted services the managers front:
    the identity provider, which the tenancy manager signs people in and
    invites them through. None is a process that signs nobody in, and every
    call that would reach the provider is refused as unavailable.

    The options after `integrations` are what the process that sweeps sets
    on the managers it purges through: each one's retention and batch. None
    keeps that manager's defaults.

    `model_prices` is what the resolver asks before it picks a model. None
    asks the one source of prices, the budgets' list table, so a model with
    no row of its own resolves nowhere.

    `models_layer` is a layer's own models: the client each model call runs
    on. None keeps the engine's: every call on the platform's key.

    `call_gate` is the budget gate every model call passes, the loop's and a
    compaction's, and `prompt_hash` the key service's hash a request's
    header records. None wires the budgets' gate behind the one, and the
    privacy namespace's keyed hash behind the other. `budget_gate` None is
    the gate over the ledger; outside `environment` `local`, a quiet null
    gate or ledger is refused at boot (`UnsafeConfiguration`).
    `artifact_seal` is what seals an artifact's text under its session's
    key, `record_seal` what seals a command's output in its transport's
    record, and `snapshot_seal` what seals a workspace's snapshot; None
    wires the privacy namespace's seal over the session keys.
    `compaction_policy` None keeps the default policy.

    Three are the adopter's for its agents: `agent_kinds`, every version of
    each kind it still runs; `principal_context`, the transition of its
    tenancy manager that answers for a principal's live permissions, which
    every tool call asks; and `result_gate`, the gate a result passes. None
    wires the tenancy manager's own, which answers for a person by the
    membership they hold at the call and for no service principal, and the
    null gate, which accepts a result and marks it unverified.

    The loop takes the rest: `tool_catalog`, the adopter's tools, after the
    engine's own (`engine_tools`), of which a session's registry holds those
    its kind names; a tool of the adopter's that takes an engine tool's name
    is refused. `attachment_reader` is what reads an attachment's text for
    the engine's read tool, None the null that refuses. Then
    `domain_classes`, the classes the adopter declares; `stream_sink`, the
    carrier its parts go to, None the quiet null, which drops them; and
    `loop_options`. Its outage signal is infra's, and its model providers
    the integrations'."""
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
        infra.get_flags(),
        media_options or MediaOptions(),
    )
    orchestrations = OrchestrationsManagerImpl(
        storage.get_orchestrations_storage(),
        tenancy,
        outbox,
        orchestrations_options or OrchestrationsOptions(),
    )
    # A product registers its resource kinds and its waiter kinds here, each
    # with its hooks, as a work kind's handler is registered in the worker,
    # and, for a kind that refuses some asks, its check (`asks`).
    leases = LeasesManagerImpl(
        storage.get_lease_storage(),
        tenancy,
        outbox,
        leases_options or LeasesOptions(),
        kinds={ResourceKind.NOOP: NoopResourceKindImpl()},
        # A session waits in line too: its sessions manager is built below,
        # so that edge is bound at call time.
        waiters={
            WaiterKind.ORCHESTRATION: OrchestrationWaiterImpl(orchestrations),
            WaiterKind.SESSION: SessionWaiterImpl(lambda: managers.agent_sessions),
        },
        work=work,
    )
    # The history first: a session's status is read off its steps. What a
    # step says reaches it through the sealing layer, by the session's policy.
    session_keys = SessionKeysImpl(storage.get_privacy_storage(), infra.get_keys())
    steps = StepsManagerImpl(
        private_history(storage, session_keys, StepStorageMemoryImpl()),
        tenancy,
        steps_options or StepsOptions(),
        # Who may instruct a session is the agents' to answer, from its
        # registry; they are built below on this manager, so the edge is
        # bound at call time.
        instructs=lambda ctx, session_id: managers.agents.require_instructor(ctx, session_id),
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
        # What the tools keep of a session outside its seal goes with its
        # key. They are built below on this manager, so the edge is bound at
        # call time.
        revoked=lambda org_id, session_id: managers.tools.erase_snapshots(org_id, session_id),
    )
    # The gate reads the budgets of a call's scopes and holds on the ledger.
    budgets = BudgetsManagerImpl(
        storage.get_budget_storage(),
        storage.get_ledger_storage(),
        tenancy,
        outbox,
        budgets_options or BudgetsOptions(),
    )
    gate = budget_gate or BudgetGateImpl(
        storage.get_budget_storage(), storage.get_ledger_storage(), BudgetGateOptions()
    )
    refuse_quiet_spend(environment, gate, storage.get_ledger_storage())
    # The one source of prices, which the resolver asks before it answers a
    # fill and the gate prices every call by.
    pricing = PricingTableImpl()
    # A session's fills. The resolver refuses a model with no price row of
    # its own.
    models = ModelsManagerImpl(
        storage.get_fill_set_storage(),
        steps,
        tenancy,
        ModelResolverTableImpl(
            model_prices or ModelPricesFromPricingImpl(pricing), ResolverOptions()
        ),
        models_options or ModelsOptions(),
    )
    attribution = AttributionManagerImpl(
        storage.get_attribution_storage(),
        agent_sessions,
        steps,
        principal_context or members_context(tenancy),
        tenancy,
        outbox,
        attribution_options or AttributionOptions(),
    )
    kinds = AgentKindCatalog(kinds=agent_kinds)
    reader = attachment_reader or AttachmentReaderNullImpl()
    # What a model request reads: rendered from the history, compacted by
    # the summarizer through the model providers, behind the gate, paid for
    # by the spender attribution names.
    providers = (
        absent_model_providers() if integrations is None else integrations.get_model_providers()
    )
    # The key each call goes out on: the platform's, unless a layer says.
    credentials = (
        CallCredentialsPlatformImpl(providers, (loop_options or LoopOptions()).credential)
        if models_layer is None
        else models_layer.credentials(providers)
    )
    # The one gate every model call passes, priced from the one source.
    calls = call_gate or CallGateBudgetImpl(gate, pricing, agent_sessions, budgets)
    windows = WindowsManagerImpl(
        storage.get_window_storage(),
        steps,
        tenancy,
        models,
        attribution,
        credentials,
        infra.get_buckets(),
        calls,
        prompt_hash or PromptHashPrivacyImpl(privacy),
        artifact_seal or ArtifactSealKeysImpl(session_keys, storage.get_privacy_storage()),
        compaction_policy or CompactionPolicy(),
        WindowsOptions(),
    )
    # The read tool pages through what windows keeps. The spawn tool starts
    # sub-agents through the agents manager, which is built below on this
    # catalog, so that edge is bound at call time.
    catalog = (
        engine_tools(steps, agent_sessions, reader, windows, lambda: managers.agents) + tool_catalog
    )
    ToolRegistry(catalog, domain_classes)  # refuses two tools of one name at boot
    # Where the engine touches the world: the session's history for a
    # person's decisions, the events for the audit of each secret a call
    # uses, the workspace and the transport infra chose, and attribution,
    # which answers whose authority each call runs under and the rule of
    # two. What a call keeps of its session's content goes under the
    # session's key: its input's hash, its command's record, and a snapshot
    # of its workspace, which holds no secret a tool of the catalog may be
    # given. A workspace base is the tenant's: built once, its claim on the
    # cache every worker shares, and scanned as a snapshot is.
    tools_settings = tools_options or ToolsOptions()
    snapshots = SnapshotStore(
        infra.get_buckets(),
        snapshot_seal or SnapshotSealKeysImpl(session_keys, storage.get_privacy_storage()),
        privacy.keyed_hash,
        infra.get_secrets(),
        injected_secrets(catalog),
        tools_settings.purge_batch,
    )
    tools = ToolsManagerImpl(
        storage.get_tool_storage(),
        steps,
        tenancy,
        events,
        outbox,
        infra.get_workspaces(),
        infra.get_transport(),
        tools_settings,
        keyed_hash=privacy.keyed_hash,
        record_seal=record_seal or RecordSealKeysImpl(session_keys, storage.get_privacy_storage()),
        attribution=attribution,
        broker=infra.get_broker(),
        snapshots=snapshots,
        bases=WorkspaceBases(
            infra.get_buckets(),
            infra.get_workspaces(),
            infra.get_transport(),
            infra.get_cache(CacheScope.WORKSPACE_BASE),
            snapshots.scan,
            tools_settings.base_build_limit,
            tools_settings.purge_batch,
            utcnow,
        ),
    )
    # A child's report reaches its parent through windows, which bounds it.
    agents = AgentsManagerImpl(
        storage.get_agent_storage(),
        agent_sessions,
        steps,
        attribution,
        result_gate or ResultGateNullImpl(),
        kinds,
        tenancy,
        outbox,
        agents_options or AgentsOptions(),
        budgets=budgets,
        windows=windows,
        tool_classes={tool.spec.name: tool.spec.authorization_class for tool in catalog},
        secret_tools=frozenset(tool.spec.name for tool in catalog if tool.spec.secrets),
        tools=tools,
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
        leases=leases,
        steps=steps,
        agent_sessions=agent_sessions,
        privacy=privacy,
        budgets=budgets,
        budgets_operator=BudgetsOperatorManagerImpl(
            storage.get_ledger_storage(), storage.get_tenancy_storage(), BudgetsOperatorOptions()
        ),
        budget_gate=gate,
        # The one source of prices: the list table.
        pricing=pricing,
        models=models,
        windows=windows,
        attribution=attribution,
        agents=agents,
        tools=tools,
        # The loop, over every namespace above: what the engine does while
        # the model chooses.
        loop=LoopManagerImpl(
            steps,
            agent_sessions,
            agents,
            kinds,
            attribution,
            models,
            windows,
            tools,
            calls,
            credentials,
            infra.get_outages(),
            stream_sink or StreamSinkNullImpl(),
            catalog,
            loop_options or LoopOptions(),
            leases=leases,
            domain_classes=domain_classes,
        ),
    )
    return managers


def injected_secrets(catalog: Iterable[ToolInterface]) -> frozenset[str]:
    """The secrets a tool of the catalog may have injected into a command, by
    name: what a workspace's snapshot is scanned for."""
    return frozenset(
        use.name for tool in catalog for use in tool.spec.secrets if use.via is SecretVia.INJECTED
    )


def engine_tools(
    steps: StepsManagerInterface,
    sessions: AgentSessionsManagerInterface,
    attachments: AttachmentReaderInterface,
    windows: WindowsManagerInterface,
    agents: Callable[[], AgentsManagerInterface],
) -> tuple[ToolInterface, ...]:
    """The tools the engine ships, offered to a session only when its kind
    names them: asking its person or standing down, writing its plan,
    reading an attachment by range, reading a result kept whole outside the
    window by the handle the window names, starting a sub-agent through the
    agents manager `agents` provides, and waiting on its sub-agents."""
    return (
        AskPersonToolImpl(),
        WritePlanToolImpl(),
        ReadAttachmentToolImpl(steps, attachments),
        ReadArtifactToolImpl(steps, windows),
        SpawnSubAgentToolImpl(sessions, agents),
        WaitForSubAgentsToolImpl(sessions),
    )
