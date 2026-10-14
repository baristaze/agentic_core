"""The key a call goes out on, over the memory storage and the scripted
provider: the loop asks a layer's call credentials before every call, and
the gate is told which key the call carries. A call whose key cannot be had
parks its session on the provider until a key is saved, with nothing held
and no usage recorded. A key the provider does not take is marked refused,
and no outage is reported for the provider; a permission the key lacks is
the call's alone, and the key stays. A tenant's own key that fails is
marked under the tenant's org: its other sessions park on it, and another
org's key of the same name still calls."""

import dataclasses
from pathlib import Path
from uuid import UUID

import pytest
from contracts.doubles import context
from contracts.loops import Clock, Loop, loop_over, reply, said

from acme.infra.base import SYSTEM_SCOPE
from acme.integrations.model_providers import ModelProvidersInterface
from acme.integrations.model_providers.calls import ModelCall
from acme.integrations.model_providers.scripted import ScriptedFailure
from acme.integrations.model_providers.types import ErrorKind, ProviderName
from acme.om.agent_steps.types.agent_step import AgentStepType
from acme.om.agent_steps.types.header import LoopOutcome, ParkReason
from acme.om.agents.impl.loop import LoopOptions
from acme.om.agents.types.run import RunEnd
from acme.om.attribution.types.principal import Principal
from acme.om.context import Role, TenantContext
from acme.om.exceptions import NoCredential
from acme.om.language_models.credentials import (
    PLATFORM_CREDENTIAL,
    CallClient,
    CallCredentialsInterface,
)
from acme.om.language_models.layer import LanguageModelsLayer
from acme.om.language_models.types.fill import Fill, ModelRole
from acme.om.root import Managers
from acme.om.windows.impl.gate import CallGateBudgetImpl
from acme.om.work.storage.impl.memory import WorkStorageMemoryImpl
from acme.om.work.types.work_item import WakeAgentSessionPayload, WorkKind

KEY = "key-1"
"""The reference a tenant's own key is named by: never the key."""


class Told(CallGateBudgetImpl):
    """The budgets' gate, keeping the credential each hold was asked for."""

    def __init__(self, managers: Managers, clock: Clock) -> None:
        super().__init__(
            managers.budget_gate, managers.pricing, managers.agent_sessions, managers.budgets, clock
        )
        self.credentials: list[str] = []

    async def authorize(
        self,
        ctx: TenantContext,
        agent_session_id: UUID,
        spender: Principal,
        role: ModelRole,
        fill: Fill,
        call: ModelCall,
        *,
        credential: str,
    ) -> UUID:
        self.credentials.append(credential)
        return await super().authorize(
            ctx, agent_session_id, spender, role, fill, call, credential=credential
        )


class Keyless(CallCredentialsInterface):
    """A tenant that holds no key for any provider."""

    async def client_for(self, ctx: TenantContext, provider: ProviderName) -> CallClient:
        raise NoCredential(provider.value, f"the tenant holds no {provider.value} key")

    async def refused(self, ctx: TenantContext, provider: ProviderName, credential: str) -> None:
        raise AssertionError("no call went out, so no key was refused")


class OwnKey(CallCredentialsInterface):
    """A tenant whose every call goes out on its own key, `KEY`, through the
    providers' clients; it keeps each key the providers refused. A layer
    builds it over the registry (`over`), once for the root and once for the
    loop, and both are this one."""

    def __init__(self) -> None:
        self._providers: ModelProvidersInterface | None = None
        self.refusals: list[tuple[ProviderName, str]] = []

    def over(self, providers: ModelProvidersInterface) -> OwnKey:
        self._providers = providers
        return self

    async def client_for(self, ctx: TenantContext, provider: ProviderName) -> CallClient:
        assert self._providers is not None
        return CallClient(credential=KEY, client=self._providers.get(provider))

    async def refused(self, ctx: TenantContext, provider: ProviderName, credential: str) -> None:
        self.refusals.append((provider, credential))


def keyed_loop(tmp_path: Path, layer: LanguageModelsLayer) -> tuple[Loop, Told]:
    """A loop whose calls go out on the key `layer` answers, behind a gate
    that keeps what it was told."""
    gates: list[Told] = []

    def told(managers: Managers, clock: Clock) -> Told:
        gates.append(Told(managers, clock))
        return gates[-1]

    loop = loop_over(tmp_path, call_gate=told, language_models_layer=layer)
    return loop, gates[0]


async def test_a_call_whose_key_cannot_be_had_parks_on_its_provider_and_spends_nothing(
    tmp_path: Path,
) -> None:
    loop, gate = keyed_loop(tmp_path, LanguageModelsLayer(credentials=lambda providers: Keyless()))
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "What is the total?")
    loop.anthropic.add(reply(said("Never asked.")))

    run = await loop.loops.run(loop.owner, agent_session_id)

    assert run.end is RunEnd.PARKED and run.park is not None
    assert (run.park.reason, run.park.unlock, run.park.retry_at) == (
        ParkReason.PROVIDER,
        "anthropic:key",
        None,
    ), "a saved key unlocks it, never a clock"
    assert run.park.cause == "the org holds no anthropic key: an admin of the org saves one"
    assert gate.credentials == [], "nothing was held"
    assert loop.anthropic.calls == [] and loop.anthropic.remaining == 1, "no call was made"
    steps = await loop.history(agent_session_id)
    assert not [step for step in steps if step.type is AgentStepType.MODEL_REQUEST]
    ledger = loop.storage.get_ledger_storage()
    assert await ledger.read_usage_records(loop.owner.org_id, agent_session_id, None, 10) == []


@pytest.mark.parametrize(
    ("status", "message", "unlock", "refused"),
    [
        (401, "invalid x-api-key", "anthropic:credential", [(ProviderName.ANTHROPIC, KEY)]),
        (403, "permission_error", "anthropic:permission", []),
    ],
    ids=["refused", "lacks-a-permission"],
)
async def test_a_key_its_provider_refuses_is_marked_refused_and_no_outage_is_reported(
    tmp_path: Path,
    status: int,
    message: str,
    unlock: str,
    refused: list[tuple[ProviderName, str]],
) -> None:
    keys = OwnKey()
    loop, gate = keyed_loop(tmp_path, LanguageModelsLayer(credentials=keys.over))
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "What is the total?")
    loop.anthropic.add(ScriptedFailure(kind=ErrorKind.CREDENTIAL, status=status, message=message))

    run = await loop.loops.run(loop.owner, agent_session_id)

    assert run.end is RunEnd.PARKED and run.park is not None
    assert (run.park.reason, run.park.unlock) == (ParkReason.PROVIDER, unlock)
    assert gate.credentials == [KEY], "the gate held the call knowing its key"
    assert keys.refusals == refused
    assert len(loop.anthropic.calls) == 1, "parked at once: no retry on the same key"
    for holder, credential in ((loop.owner.org_id, KEY), (SYSTEM_SCOPE, PLATFORM_CREDENTIAL)):
        assert await loop.outages.current(holder, "anthropic", credential) is None, credential


async def test_a_tenant_keys_outage_is_marked_under_its_org_and_parks_that_org_alone(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path, language_models_layer=LanguageModelsLayer(credentials=OwnKey().over))
    learner = await loop.start()
    await loop.say(learner, "What is the total?")
    overloaded = ScriptedFailure(kind=ErrorKind.OVERLOADED, retry_after=2)
    loop.anthropic.add(overloaded, overloaded, overloaded)
    loop.openai.add(reply(said("The total is 12."), model="gpt-6.1-sol"))

    await loop.loops.run(loop.owner, learner)

    assert len(loop.anthropic.calls) == 3, "two retries in process, then the outage"
    mark = await loop.outages.current(loop.owner.org_id, "anthropic", KEY)
    assert mark is not None, "the tenant's key is marked under the tenant's org"
    for credential in (KEY, loop.credential):
        assert await loop.outages.current(SYSTEM_SCOPE, "anthropic", credential) is None

    second = await loop.start()
    await loop.say(second, "And the average?")
    parked = await loop.loops.run(loop.owner, second)

    assert parked.end is RunEnd.PARKED and parked.park is not None
    assert parked.park.retry_at == mark.retry_at
    assert len(loop.anthropic.calls) == 3, "the same org parks without a call"

    other = dataclasses.replace(loop, owner=context(Role.OWNER))
    third = await other.start()
    await other.say(third, "Hello?")
    loop.anthropic.add(reply(said("Hi.")))
    answered = await other.loops.run(other.owner, third)

    assert answered.outcome is LoopOutcome.SUCCEEDED
    assert len(loop.anthropic.calls) == 4, "another org's key of the same name still calls"


@pytest.mark.parametrize(
    ("own", "kind", "status", "message", "unlock", "cause"),
    [
        (
            False,
            ErrorKind.BILLING,
            402,
            "credit balance is too low",
            "anthropic:billing",
            "anthropic refused the call for its account's billing: "
            + "an operator of the platform settles it",
        ),
        (
            False,
            ErrorKind.CREDENTIAL,
            401,
            "invalid x-api-key",
            "anthropic:credential",
            "anthropic did not take the key: an operator of the platform replaces it",
        ),
        (
            True,
            ErrorKind.CREDENTIAL,
            403,
            "permission_error",
            "anthropic:permission",
            "the key lacks what this call needs, a permission, a region, or a model: "
            + "an admin of the org grants it",
        ),
    ],
    ids=["platform-billing", "platform-key-refused", "own-key-lacks-a-permission"],
)
async def test_a_park_on_a_key_or_an_account_says_who_acts_and_wakes_once_it_is_fixed(
    tmp_path: Path,
    own: bool,
    kind: ErrorKind,
    status: int,
    message: str,
    unlock: str,
    cause: str,
) -> None:
    """The fix is made at the provider, or where the platform keeps its key,
    and nothing tells the engine: the park says who must act, an operator
    for the platform's key and an admin for the org's, and tries again by
    itself, so the next run after the fix goes on."""
    loop = loop_over(
        tmp_path,
        language_models_layer=LanguageModelsLayer(credentials=OwnKey().over) if own else None,
    )
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "What is the total?")
    loop.anthropic.add(ScriptedFailure(kind=kind, status=status, message=message))

    run = await loop.loops.run(loop.owner, agent_session_id)

    park = run.park
    assert run.end is RunEnd.PARKED and park is not None and park.retry_at is not None
    assert (park.reason, park.unlock, park.cause) == (ParkReason.PROVIDER, unlock, cause)
    assert park.retry_at == loop.clock.now + LoopOptions().fix_wait, "it tries again by itself"
    work = loop.storage.get_work_storage()
    assert isinstance(work, WorkStorageMemoryImpl)
    wakes = [
        item
        for _, item in work._items.values()  # pyright: ignore[reportPrivateUsage]
        if item.kind is WorkKind.WAKE_AGENT_SESSION and item.target_id == agent_session_id
    ]
    assert [WakeAgentSessionPayload.model_validate(w.payload).not_before for w in wakes] == [
        park.retry_at
    ], "the queue holds its wake until the retry time"

    loop.clock.now = park.retry_at
    await loop.managers.agent_sessions.wake_agent_session(loop.owner, agent_session_id, park)
    loop.anthropic.add(reply(said("The total is 12.")))
    resumed = await loop.loops.run(loop.owner, agent_session_id)

    assert resumed.outcome is LoopOutcome.SUCCEEDED, "the fixed key answers"
    assert len(loop.anthropic.calls) == 2
