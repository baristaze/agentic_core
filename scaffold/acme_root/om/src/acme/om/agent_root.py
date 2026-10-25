"""The engine's root: every manager of the agent, built in dependency order
over the guideline's managers it reads, and handed back as one frozen
object. `acme.om.root` calls it once from `build_managers`, and its
`Managers` holds these fields beside its own. What an adopter sets on the
engine's managers is one options object, `AgentRootOptions`."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from acme.infra.base import QuietNull
from acme.infra.cache import CacheScope
from acme.infra.root import InfraInterface
from acme.integrations.model_providers.registry import absent_model_providers
from acme.integrations.root import IntegrationsInterface
from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.agent_sessions.impl.manager import AgentSessionsManagerImpl, AgentSessionsOptions
from acme.om.agent_sessions.kinds import AGENT_SESSION_WAITER
from acme.om.agent_steps import AgentStepsManagerInterface
from acme.om.agent_steps.impl.manager import AgentStepsManagerImpl, AgentStepsOptions
from acme.om.agent_steps.storage import AgentStepStorageInterface
from acme.om.agent_steps.storage.impl.memory import AgentStepStorageMemoryImpl
from acme.om.agents import AgentsManagerInterface, ResultGateInterface
from acme.om.agents.hooks import LoopHooksInterface
from acme.om.agents.impl.gate import ResultGateNullImpl
from acme.om.agents.impl.hooks import LoopHooksNullImpl
from acme.om.agents.impl.loop import LoopManagerImpl, LoopOptions
from acme.om.agents.impl.manager import AgentsManagerImpl, AgentsOptions
from acme.om.agents.impl.sink import StreamSinkNullImpl
from acme.om.agents.impl.waiter import AgentSessionWaiterImpl
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
from acme.om.change_events import ChangeEventsManagerInterface
from acme.om.context import TenantContext
from acme.om.exceptions import NotFound, UnsafeConfiguration
from acme.om.language_models.impl.credentials import CallCredentialsPlatformImpl
from acme.om.language_models.impl.manager import LanguageModelsManagerImpl, LanguageModelsOptions
from acme.om.language_models.impl.prices import ModelPricesFromPricingImpl
from acme.om.language_models.impl.resolver import ModelResolverTableImpl, ResolverOptions
from acme.om.language_models.layer import LanguageModelsLayer
from acme.om.language_models.manager import LanguageModelsManagerInterface
from acme.om.language_models.prices import ModelPricesInterface
from acme.om.leases import LeasesManagerInterface
from acme.om.leases.hooks import WaiterInterface
from acme.om.outbox import OutboxRelayInterface
from acme.om.privacy import PrivacyManagerInterface
from acme.om.privacy.impl.keys import AgentSessionKeysImpl
from acme.om.privacy.impl.manager import PrivacyManagerImpl, PrivacyOptions
from acme.om.privacy.impl.memory_only_agent_steps import AgentStepStorageShapeOnlyImpl
from acme.om.privacy.impl.records import RecordSealKeysImpl
from acme.om.privacy.impl.routed_agent_steps import AgentStepStorageRoutedImpl
from acme.om.privacy.impl.sealed_agent_steps import AgentStepStorageSealedImpl
from acme.om.privacy.impl.snapshots import SnapshotSealKeysImpl
from acme.om.privacy.impl.titles import TitleSealKeysImpl
from acme.om.privacy.impl.tool_outputs import ToolOutputSealKeysImpl
from acme.om.privacy.keys import AgentSessionKeysInterface
from acme.om.spend_budgets import (
    SpendBudgetGateInterface,
    SpendBudgetsManagerInterface,
    SpendBudgetsOperatorManagerInterface,
)
from acme.om.spend_budgets.impl.gate import SpendBudgetGateImpl, SpendBudgetGateOptions
from acme.om.spend_budgets.impl.manager import SpendBudgetsManagerImpl, SpendBudgetsOptions
from acme.om.spend_budgets.impl.operator import (
    SpendBudgetsOperatorManagerImpl,
    SpendBudgetsOperatorOptions,
)
from acme.om.spend_budgets.impl.pricing import PricingTableImpl
from acme.om.spend_budgets.pricing import PricingInterface
from acme.om.storage.root import StorageInterface
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tools import ToolRegistry, ToolsManagerInterface
from acme.om.tools.attachments import AttachmentReaderInterface
from acme.om.tools.impl.attachments import AttachmentReaderNullImpl
from acme.om.tools.impl.bases import AgentWorkspaceBases
from acme.om.tools.impl.manager import ToolsManagerImpl, ToolsOptions
from acme.om.tools.impl.snapshots import SnapshotStore
from acme.om.tools.native import native_tools
from acme.om.tools.rules import injected_secrets
from acme.om.tools.seal import RecordSealInterface, SnapshotSealInterface
from acme.om.tools.tool import ToolInterface
from acme.om.windows import WindowsManagerInterface
from acme.om.windows.gate import CallGateInterface
from acme.om.windows.hashes import PromptHashInterface
from acme.om.windows.impl.gate import CallGateSpendBudgetImpl
from acme.om.windows.impl.hashes import PromptHashPrivacyImpl
from acme.om.windows.impl.manager import WindowsManagerImpl, WindowsOptions
from acme.om.windows.seal import ToolOutputSealInterface
from acme.om.windows.types.policy import CompactionPolicy

LOCAL = "local"
"""The one environment a root accepts a quiet null budget gate or ledger in."""


@dataclass(frozen=True)
class AgentManagers:
    """The engine's managers, each a field of the guideline's `Managers` as
    well."""

    agent_steps: AgentStepsManagerInterface
    agent_sessions: AgentSessionsManagerInterface
    privacy: PrivacyManagerInterface
    spend_budgets: SpendBudgetsManagerInterface
    spend_budgets_operator: SpendBudgetsOperatorManagerInterface
    spend_budget_gate: SpendBudgetGateInterface
    pricing: PricingInterface
    language_models: LanguageModelsManagerInterface
    windows: WindowsManagerInterface
    attribution: AttributionManagerInterface
    agents: AgentsManagerInterface
    tools: ToolsManagerInterface
    loop: LoopManagerInterface


@dataclass(frozen=True)
class AgentRootOptions:
    """What an adopter sets on the engine's managers. None keeps a manager's
    defaults, or wires the engine's own impl or its null.

    The options of the steps, the sessions, the budgets, the language
    models, the agents, attribution, and the tools are each manager's
    retention, batch, and bounds, as the process that sweeps sets them.

    `model_prices` is what the resolver asks before it picks a model. None
    asks the one source of prices, the budgets' list table, so a model with
    no row of its own resolves nowhere. `language_models_layer` is a layer's
    own models: the client each model call runs on. None keeps the engine's:
    every call on the platform's key.

    `call_gate` is the budget gate every model call passes, the loop's and a
    compaction's, and `prompt_hash` the key service's hash a request's
    header records. None wires the budgets' gate behind the one, and the
    privacy namespace's keyed hash behind the other. `spend_budget_gate`
    None is the gate over the ledger; outside `environment` `local`, a quiet
    null gate or ledger is refused at boot (`UnsafeConfiguration`).
    `tool_output_seal` is what seals a tool output's text under its
    session's key, `record_seal` what seals a command's output in its
    transport's record, and `snapshot_seal` what seals a workspace's
    snapshot; None wires the privacy namespace's seal over the session keys.
    `compaction_policy` None keeps the default policy.

    Three are the adopter's for its agents: `agent_kinds`, every version of
    each kind it still runs; `principal_context`, the transition of its
    tenancy manager that answers for a principal's live permissions, which
    every tool call asks; and `result_gate`, the gate a result passes. None
    wires the tenancy manager's own, which answers for a person by the
    membership they hold at the call and for no service principal, and the
    null gate, which accepts a result and marks it unverified.

    The loop takes the rest: `tool_catalog`, the adopter's tools, after the
    engine's own (`native_tools`), of which a session's registry holds those
    its kind names; a tool of the adopter's that takes an engine tool's name
    is refused. `attachment_reader` is what reads an attachment's text for
    the engine's read tool, None the null that refuses. Then
    `domain_classes`, the classes the adopter declares; `stream_sink`, the
    carrier its parts go to, None the quiet null, which drops them;
    `loop_options`; and `loop_hooks`, where a layer above extends each run,
    None the null, which extends nothing. Its outage signal is infra's, and
    its model providers the integrations'."""

    steps_options: AgentStepsOptions | None = None
    agent_sessions_options: AgentSessionsOptions | None = None
    budgets_options: SpendBudgetsOptions | None = None
    language_models_options: LanguageModelsOptions | None = None
    model_prices: ModelPricesInterface | None = None
    language_models_layer: LanguageModelsLayer | None = None
    call_gate: CallGateInterface | None = None
    prompt_hash: PromptHashInterface | None = None
    tool_output_seal: ToolOutputSealInterface | None = None
    record_seal: RecordSealInterface | None = None
    snapshot_seal: SnapshotSealInterface | None = None
    compaction_policy: CompactionPolicy | None = None
    agents_options: AgentsOptions | None = None
    attribution_options: AttributionOptions | None = None
    agent_kinds: tuple[AgentKind, ...] = ()
    principal_context: PrincipalContext | None = None
    result_gate: ResultGateInterface | None = None
    tools_options: ToolsOptions | None = None
    spend_budget_gate: SpendBudgetGateInterface | None = None
    stream_sink: StreamSinkInterface | None = None
    tool_catalog: tuple[ToolInterface, ...] = ()
    attachment_reader: AttachmentReaderInterface | None = None
    domain_classes: tuple[str, ...] = ()
    loop_options: LoopOptions | None = None
    loop_hooks: LoopHooksInterface | None = None
    environment: str = LOCAL


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
    managers: AgentManagers, org_id: UUID, agent_session_id: UUID, tree_id: UUID | None
) -> None:
    """What the windows, the tools, attribution, and the agents hold of a
    session the sweep purges: its tool outputs, and its workspace with its
    transport's records, which go with its history, its authority, and its
    tree when it was the tree's last session."""
    await managers.windows.purge_tool_outputs(org_id, agent_session_id)
    await managers.tools.purge_agent_workspace(org_id, agent_session_id)
    await managers.attribution.purge_authority(org_id, agent_session_id)
    if tree_id is not None:
        await managers.agents.purge_tree(org_id, tree_id)


async def opens(managers: AgentManagers, ctx: TenantContext, agent_session_id: UUID) -> None:
    """A history, or what its session keeps outside it, opens to whom the
    session is visible: one it is not visible to finds nothing there."""
    if not await managers.agent_sessions.is_visible(ctx, agent_session_id):
        raise NotFound(f"agent session {agent_session_id} not found")


def private_history(
    storage: StorageInterface, keys: AgentSessionKeysInterface, transient: AgentStepStorageInterface
) -> AgentStepStorageInterface:
    """The history as the engine reaches it: every session routed by its
    storage policy to content sealed at rest, to a shape at rest and its
    content in this process, or to nothing at rest (`transient`). Each impl
    is wired here, at boot; the steps manager sees one interface."""
    history = storage.get_agent_step_storage()
    return AgentStepStorageRoutedImpl(
        sealed=AgentStepStorageSealedImpl(history, keys),
        shape_only=AgentStepStorageShapeOnlyImpl(history),
        transient=transient,
        policies=storage.get_privacy_storage(),
    )


def agent_session_waiters(
    managers: Callable[[], AgentManagers],
) -> dict[str, WaiterInterface]:
    """The waiter a session waits in a line of the leases as. Its sessions
    manager is built after the leases manager, so that edge is bound at
    call time, through `managers`."""
    return {AGENT_SESSION_WAITER: AgentSessionWaiterImpl(lambda: managers().agent_sessions)}


def build_agent_managers(
    storage: StorageInterface,
    infra: InfraInterface,
    integrations: IntegrationsInterface | None,
    *,
    tenancy: TenancyManagerInterface,
    change_events: ChangeEventsManagerInterface,
    outbox: OutboxRelayInterface,
    leases: LeasesManagerInterface,
    options: AgentRootOptions,
) -> AgentManagers:
    """The engine's managers over the guideline's `tenancy`, `change_events`,
    `outbox`, and `leases`, as `options` sets them. `integrations` None is a
    process with no model provider: every call is refused as unavailable."""
    # The history first: a session's status is read off its steps. What a
    # step says reaches it through the sealing layer, by the session's policy.
    agent_session_keys = AgentSessionKeysImpl(storage.get_privacy_storage(), infra.get_keys())
    steps = AgentStepsManagerImpl(
        private_history(storage, agent_session_keys, AgentStepStorageMemoryImpl()),
        tenancy,
        options.steps_options or AgentStepsOptions(),
        # Who may instruct a session is the agents' to answer, from its
        # registry; who steers it is attribution's, and who sees it the
        # sessions'. Each is built below on this manager, so the edges are
        # bound at call time.
        instructs=lambda ctx, agent_session_id: managers.agents.require_instructor(
            ctx, agent_session_id
        ),
        steers=lambda ctx, agent_session_id: managers.attribution.steers(ctx, agent_session_id),
        opens=lambda ctx, agent_session_id: opens(managers, ctx, agent_session_id),
    )
    agent_sessions = AgentSessionsManagerImpl(
        storage.get_agent_session_storage(),
        steps,
        tenancy,
        outbox,
        options.agent_sessions_options or AgentSessionsOptions(),
        # What attribution and the agents hold of a purged session goes with
        # it. Both are built below on this manager, so the edge is bound at
        # call time.
        purged=lambda org_id, agent_session_id, tree_id: purge_held(
            managers, org_id, agent_session_id, tree_id
        ),
        # A title is content: sealed on its way in, under a key of the
        # session's own that the privacy namespace keeps (ADR 1042).
        titles=TitleSealKeysImpl(agent_session_keys),
    )
    privacy = PrivacyManagerImpl(
        storage.get_privacy_storage(),
        agent_session_keys,
        steps,
        agent_sessions,
        tenancy,
        outbox,
        PrivacyOptions(),
        # What the tools keep of a session outside its seal goes with its
        # key. They are built below on this manager, so the edge is bound at
        # call time.
        revoked=lambda ctx, agent_session_id: managers.tools.erase_snapshots(ctx, agent_session_id),
    )
    # The gate reads the budgets of a call's scopes and holds on the ledger.
    budgets = SpendBudgetsManagerImpl(
        storage.get_spend_budget_storage(),
        storage.get_ledger_storage(),
        tenancy,
        outbox,
        options.budgets_options or SpendBudgetsOptions(),
    )
    gate = options.spend_budget_gate or SpendBudgetGateImpl(
        storage.get_spend_budget_storage(), storage.get_ledger_storage(), SpendBudgetGateOptions()
    )
    refuse_quiet_spend(options.environment, gate, storage.get_ledger_storage())
    # The one source of prices, which the resolver asks before it answers a
    # fill and the gate prices every call by.
    pricing = PricingTableImpl()
    # A session's fills. The resolver refuses a model with no price row of
    # its own.
    models = LanguageModelsManagerImpl(
        storage.get_fill_set_storage(),
        steps,
        tenancy,
        ModelResolverTableImpl(
            options.model_prices or ModelPricesFromPricingImpl(pricing), ResolverOptions()
        ),
        options.language_models_options or LanguageModelsOptions(),
    )
    attribution = AttributionManagerImpl(
        storage.get_attribution_storage(),
        agent_sessions,
        steps,
        options.principal_context or members_context(tenancy),
        tenancy,
        outbox,
        options.attribution_options or AttributionOptions(),
    )
    kinds = AgentKindCatalog(kinds=options.agent_kinds)
    reader = options.attachment_reader or AttachmentReaderNullImpl()
    # What a model request reads: rendered from the history, compacted by
    # the summarizer through the model providers, behind the gate, paid for
    # by the spender attribution names.
    providers = (
        absent_model_providers() if integrations is None else integrations.get_model_providers()
    )
    # The key each call goes out on: the platform's, unless a layer says.
    credentials = (
        CallCredentialsPlatformImpl(providers, (options.loop_options or LoopOptions()).credential)
        if options.language_models_layer is None
        else options.language_models_layer.credentials(providers)
    )
    # The one gate every model call passes, priced from the one source.
    calls = options.call_gate or CallGateSpendBudgetImpl(gate, pricing, agent_sessions, budgets)
    windows = WindowsManagerImpl(
        storage.get_window_storage(),
        steps,
        tenancy,
        models,
        attribution,
        credentials,
        infra.get_buckets(),
        calls,
        options.prompt_hash or PromptHashPrivacyImpl(privacy),
        options.tool_output_seal
        or ToolOutputSealKeysImpl(agent_session_keys, storage.get_privacy_storage()),
        options.compaction_policy or CompactionPolicy(),
        WindowsOptions(),
        opens=lambda ctx, agent_session_id: opens(managers, ctx, agent_session_id),
    )
    # The read tool pages through what windows keeps. The spawn tool starts
    # sub-agents through the agents manager, which is built below on this
    # catalog, so that edge is bound at call time.
    catalog = (
        native_tools(steps, agent_sessions, reader, windows, lambda: managers.agents)
        + options.tool_catalog
    )
    ToolRegistry(catalog, options.domain_classes)  # refuses two tools of one name at boot
    # Where the engine touches the world: the session's history for a
    # person's decisions, the change events for the audit of each secret a call
    # uses, the workspace and the transport infra chose, and attribution,
    # which answers whose authority each call runs under and the rule of
    # two. What a call keeps of its session's content goes under the
    # session's key: its input's hash, its command's record, and a snapshot
    # of its workspace, which holds no secret a tool of the catalog, or one
    # the session's hooks attach, may be given. A workspace base is the
    # tenant's: built once, its claim on the cache every worker shares, and
    # scanned as a snapshot is.
    tools_settings = options.tools_options or ToolsOptions()
    snapshots = SnapshotStore(
        infra.get_buckets(),
        options.snapshot_seal
        or SnapshotSealKeysImpl(agent_session_keys, storage.get_privacy_storage()),
        privacy.keyed_hash,
        infra.get_secrets(),
        injected_secrets(tool.spec for tool in catalog),
        tools_settings.purge_batch,
    )
    tools = ToolsManagerImpl(
        storage.get_tool_storage(),
        steps,
        tenancy,
        change_events,
        outbox,
        infra.get_agent_workspaces(),
        infra.get_transport(),
        tools_settings,
        keyed_hash=privacy.keyed_hash,
        record_seal=options.record_seal
        or RecordSealKeysImpl(agent_session_keys, storage.get_privacy_storage()),
        attribution=attribution,
        broker=infra.get_broker(),
        snapshots=snapshots,
        bases=AgentWorkspaceBases(
            infra.get_buckets(),
            infra.get_agent_workspaces(),
            infra.get_transport(),
            infra.get_cache(CacheScope.AGENT_WORKSPACE_BASE),
            snapshots.scan,
            tools_settings.base_build_limit,
            tools_settings.purge_batch,
            utcnow,
        ),
    )
    # A child's report reaches its parent through windows, which bounds it.
    # The toolsets a layer's hooks attach to a session are the loop's, and
    # the agents' too: who may instruct the session classes their tools, and
    # what a session carries to another counts their secrets.
    hooks = options.loop_hooks or LoopHooksNullImpl()
    agents = AgentsManagerImpl(
        storage.get_agent_storage(),
        agent_sessions,
        steps,
        attribution,
        options.result_gate or ResultGateNullImpl(),
        kinds,
        tenancy,
        outbox,
        options.agents_options or AgentsOptions(),
        budgets=budgets,
        windows=windows,
        tool_classes={tool.spec.name: tool.spec.authorization_class for tool in catalog},
        secret_tools=frozenset(tool.spec.name for tool in catalog if tool.spec.secrets),
        tools=tools,
        hooks=hooks,
    )
    managers = AgentManagers(
        agent_steps=steps,
        agent_sessions=agent_sessions,
        privacy=privacy,
        spend_budgets=budgets,
        spend_budgets_operator=SpendBudgetsOperatorManagerImpl(
            storage.get_ledger_storage(),
            storage.get_tenancy_storage(),
            SpendBudgetsOperatorOptions(),
        ),
        spend_budget_gate=gate,
        # The one source of prices: the list table.
        pricing=pricing,
        language_models=models,
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
            options.stream_sink or StreamSinkNullImpl(),
            catalog,
            options.loop_options or LoopOptions(),
            leases=leases,
            domain_classes=options.domain_classes,
            hooks=hooks,
        ),
    )
    return managers
