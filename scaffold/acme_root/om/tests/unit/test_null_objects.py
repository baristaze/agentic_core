"""Every interface the engine reaches has a null object a root can wire. A
quiet one does nothing and says so: it carries the mark, and its answer is
the declared degraded one. A loud one refuses with a typed error. A root
outside `local` refuses a quiet budget gate at boot."""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from acme.infra.agent_workspaces import (
    AgentWorkspace,
    AgentWorkspaceProviderInterface,
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationSpec,
)
from acme.infra.agent_workspaces.twin import AgentWorkspaceNullImpl
from acme.infra.base import QuietNull
from acme.infra.exceptions import InfraException
from acme.infra.impl.local import InfraLocalImpl
from acme.infra.keys import KeyServiceInterface
from acme.infra.keys.null import KeyServiceNullImpl
from acme.infra.transports import (
    CommandSpec,
    CredentialBrokerInterface,
    SecretUse,
    SecretVia,
    TransportInterface,
)
from acme.infra.transports.broker import BrokerNullImpl
from acme.infra.transports.twin import RecordSealTwin, TransportNullImpl
from acme.integrations.model_providers import ModelProviderInterface, reply_of
from acme.integrations.model_providers.absent import ModelProviderAbsentImpl
from acme.integrations.model_providers.calls import ModelCall
from acme.integrations.model_providers.types import ProviderName
from acme.om.agent_root import AgentRootOptions
from acme.om.agent_steps.types.stream import TextPart
from acme.om.agents import LoopHooksInterface, ResultGateInterface
from acme.om.agents.hooks import NO_ATTACHED_TOOLSETS
from acme.om.agents.impl.gate import ResultGateNullImpl
from acme.om.agents.impl.hooks import LoopHooksNullImpl
from acme.om.agents.impl.sink import StreamSinkNullImpl
from acme.om.agents.sink import StreamSinkInterface
from acme.om.agents.types.result import Claim, Result
from acme.om.attribution.impl.manager import no_principal_context
from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.base import utcnow
from acme.om.context import RequestContext, Role
from acme.om.exceptions import OmException, UnsafeConfiguration
from acme.om.language_models.impl.prices import ModelPricesNullImpl
from acme.om.language_models.prices import ModelPricesInterface
from acme.om.root import build_managers
from acme.om.spend_budgets import SpendBudgetGateInterface
from acme.om.spend_budgets.impl.gate import SpendBudgetGateNullImpl
from acme.om.spend_budgets.types.amount import Spend
from acme.om.spend_budgets.types.spend_hold import SpendHold, SpendHoldRequest
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.tools.impl.seal import RecordSealNullImpl
from acme.om.tools.seal import RecordSealInterface
from acme.om.windows.gate import CallGateInterface
from acme.om.windows.hashes import PromptHashInterface
from acme.om.windows.impl.gate import CallGateNullImpl
from acme.om.windows.impl.hashes import PromptHashNullImpl
from acme.om.windows.impl.seal import ToolOutputSealNullImpl
from acme.om.windows.seal import ToolOutputSealInterface

from contracts.agent_session_storage import make_agent_session
from contracts.doubles import APP, context

CTX = context(Role.OWNER)
PAYER = Principal(kind=PrincipalKind.PERSON, id=CTX.user_id)
CALL = ModelCall(model="any", messages=(), max_output_tokens=1)
NONE_SPEC = IsolationSpec(mode=IsolationMode.CONTAINER, egress=EgressPolicy(mode=EgressMode.NONE))
ABSENT = AgentWorkspace.absent(CTX.org_id, uuid4())


def refusing(act: Callable[[], Awaitable[Any]]) -> Callable[[], Awaitable[BaseException]]:
    async def refused() -> BaseException:
        try:
            await act()
        except (OmException, InfraException) as error:
            return error
        raise AssertionError("a loud null did something")

    return refused


LOUD: list[tuple[object, object, Callable[[], Awaitable[Any]]]] = [
    (
        CallGateInterface,
        CallGateNullImpl(),
        lambda: CallGateNullImpl().authorize(
            CTX,
            uuid4(),
            PAYER,
            "main",
            None,  # type: ignore[arg-type]
            CALL,
            credential="platform",
        ),
    ),
    (
        PromptHashInterface,
        PromptHashNullImpl(),
        lambda: PromptHashNullImpl().keyed_hash(CTX, uuid4(), b"a prompt"),
    ),
    (
        ToolOutputSealInterface,
        ToolOutputSealNullImpl(),
        lambda: ToolOutputSealNullImpl().seal(CTX, uuid4(), uuid4(), b"a result"),
    ),
    (
        RecordSealInterface,
        RecordSealNullImpl(),
        lambda: RecordSealNullImpl().seal(CTX, uuid4(), uuid4(), b"what it printed"),
    ),
    (
        KeyServiceInterface,
        KeyServiceNullImpl(),
        lambda: KeyServiceNullImpl().generate(CTX.org_id, uuid4(), 1),
    ),
    (
        TransportInterface,
        TransportNullImpl(),
        lambda: TransportNullImpl().run(
            ABSENT,
            CommandSpec(argv=("true",), key=uuid4(), epoch=1, deadline=utcnow(), max_output=1),
            seal=RecordSealTwin().seal,
        ),
    ),
    (
        CredentialBrokerInterface,
        BrokerNullImpl(),
        lambda: BrokerNullImpl().attach(
            ABSENT,
            uuid4(),
            SecretUse(name="a-key", via=SecretVia.BROKERED, destination="api.example.test"),
        ),
    ),
    (
        AgentWorkspaceProviderInterface,
        AgentWorkspaceNullImpl(),
        lambda: AgentWorkspaceNullImpl().prepare(CTX.org_id, uuid4(), NONE_SPEC),
    ),
    (
        ModelProviderInterface,
        ModelProviderAbsentImpl(ProviderName.ANTHROPIC),
        lambda: reply_of(ModelProviderAbsentImpl(ProviderName.ANTHROPIC).stream(CALL)),
    ),
    (
        Callable,
        no_principal_context,
        lambda: no_principal_context(
            RequestContext(request_id=uuid4(), app=APP), CTX.org_id, PAYER
        ),
    ),
]
"""What acts, or spends, or seals: each refuses, and says why."""


@pytest.mark.parametrize(
    ("interface", "null", "act"), LOUD, ids=lambda v: getattr(v, "__name__", "")
)
async def test_a_loud_null_refuses_with_a_typed_error_and_is_never_quiet(
    interface: object, null: object, act: Callable[[], Awaitable[Any]]
) -> None:
    if isinstance(interface, type):
        assert isinstance(null, interface)
    assert not isinstance(null, QuietNull)
    error = await refusing(act)()
    assert getattr(error, "code", None), "a typed error, with its code"


async def test_the_quiet_nulls_do_nothing_and_say_so() -> None:
    gate = SpendBudgetGateNullImpl()
    sink = StreamSinkNullImpl()
    verdicts = ResultGateNullImpl()
    hooks = LoopHooksNullImpl()
    for interface, null in (
        (SpendBudgetGateInterface, gate),
        (StreamSinkInterface, sink),
        (ResultGateInterface, verdicts),
        (LoopHooksInterface, hooks),
    ):
        assert isinstance(null, interface) and isinstance(null, QuietNull)
    request = SpendHoldRequest(
        spender_id=PAYER.id, scopes=(), exposure=Spend(cost_micros=10, tokens=10), purpose="main"
    )
    hold = await gate.authorize(CTX, request)
    assert isinstance(hold, SpendHold) and hold.lines == (), "a hold on no line bounds nothing"
    sink.emit(TextPart(agent_session_id=uuid4(), agent_step_id=uuid4(), n=0, index=0, text="lost"))
    verdict = await verdicts.check(CTX, uuid4(), Result(claim=Claim.SUCCEEDED, evidence=(uuid4(),)))
    assert verdict.accepted and not verdict.verified, "accepted, and marked unverified"
    assert not ModelPricesNullImpl().priced(ProviderName.ANTHROPIC, "any"), "no price is guessed"
    assert isinstance(ModelPricesNullImpl(), ModelPricesInterface)
    session = make_agent_session()
    assert await hooks.attached_toolsets(CTX, session) == NO_ATTACHED_TOOLSETS, "its kind's alone"


@pytest.mark.parametrize("environment", ["test", "dev", "staging", "production"])
def test_a_root_outside_local_refuses_a_quiet_budget_gate(tmp_path: Path, environment: str) -> None:
    with pytest.raises(UnsafeConfiguration, match="SpendBudgetGateNullImpl"):
        build_managers(
            StorageMemoryImpl(),
            InfraLocalImpl(tmp_path),
            agent_root_options=AgentRootOptions(
                spend_budget_gate=SpendBudgetGateNullImpl(), environment=environment
            ),
        )


def test_a_local_root_accepts_a_quiet_budget_gate_and_any_root_its_own(tmp_path: Path) -> None:
    local = build_managers(
        StorageMemoryImpl(),
        InfraLocalImpl(tmp_path),
        agent_root_options=AgentRootOptions(spend_budget_gate=SpendBudgetGateNullImpl()),
    )
    assert isinstance(local.spend_budget_gate, QuietNull)
    deployed = build_managers(
        StorageMemoryImpl(),
        InfraLocalImpl(tmp_path),
        agent_root_options=AgentRootOptions(environment="production"),
    )
    assert not isinstance(deployed.spend_budget_gate, QuietNull)
