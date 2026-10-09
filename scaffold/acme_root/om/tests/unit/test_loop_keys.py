"""The key a call goes out on, over the memory storage and the scripted
provider: the loop asks a layer's call credentials before every call, and
the gate is told which key the call carries. A call whose key cannot be had
parks its session on the provider until a key is saved, with nothing held
and no usage recorded. A key the provider does not take is marked refused,
and no outage is reported for the provider; a permission the key lacks is
the call's alone, and the key stays."""

from pathlib import Path
from uuid import UUID

import pytest
from contracts.loops import Clock, Loop, loop_over, reply, said

from acme.infra.base import SYSTEM_SCOPE
from acme.integrations.model_providers import ModelProvidersInterface
from acme.integrations.model_providers.calls import ModelCall
from acme.integrations.model_providers.scripted import ScriptedFailure
from acme.integrations.model_providers.types import ErrorKind, ProviderName
from acme.om.agents.types.run import RunEnd
from acme.om.attribution.types.principal import Principal
from acme.om.context import TenantContext
from acme.om.exceptions import NoCredential
from acme.om.models.credentials import PLATFORM_CREDENTIAL, CallClient, CallCredentialsInterface
from acme.om.models.layer import ModelsLayer
from acme.om.models.types.fill import Fill, ModelRole
from acme.om.root import Managers
from acme.om.steps.types.header import ParkReason
from acme.om.steps.types.step import StepType
from acme.om.windows.impl.gate import CallGateBudgetImpl

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
        session_id: UUID,
        spender: Principal,
        role: ModelRole,
        fill: Fill,
        call: ModelCall,
        *,
        credential: str,
    ) -> UUID:
        self.credentials.append(credential)
        return await super().authorize(
            ctx, session_id, spender, role, fill, call, credential=credential
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


def keyed_loop(tmp_path: Path, layer: ModelsLayer) -> tuple[Loop, Told]:
    """A loop whose calls go out on the key `layer` answers, behind a gate
    that keeps what it was told."""
    gates: list[Told] = []

    def told(managers: Managers, clock: Clock) -> Told:
        gates.append(Told(managers, clock))
        return gates[-1]

    loop = loop_over(tmp_path, call_gate=told, models_layer=layer)
    return loop, gates[0]


async def test_a_call_whose_key_cannot_be_had_parks_on_its_provider_and_spends_nothing(
    tmp_path: Path,
) -> None:
    loop, gate = keyed_loop(tmp_path, ModelsLayer(credentials=lambda providers: Keyless()))
    session_id = await loop.start()
    await loop.say(session_id, "What is the total?")
    loop.anthropic.add(reply(said("Never asked.")))

    run = await loop.loops.run(loop.owner, session_id)

    assert run.end is RunEnd.PARKED and run.park is not None
    assert (run.park.reason, run.park.unlock, run.park.retry_at) == (
        ParkReason.PROVIDER,
        "anthropic:key",
        None,
    ), "a saved key unlocks it, never a clock"
    assert gate.credentials == [], "nothing was held"
    assert loop.anthropic.calls == [] and loop.anthropic.remaining == 1, "no call was made"
    steps = await loop.history(session_id)
    assert not [step for step in steps if step.type is StepType.MODEL_REQUEST]
    ledger = loop.storage.get_ledger_storage()
    assert await ledger.read_usage_records(loop.owner.org_id, session_id, None, 10) == []


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
    loop, gate = keyed_loop(tmp_path, ModelsLayer(credentials=keys.over))
    session_id = await loop.start()
    await loop.say(session_id, "What is the total?")
    loop.anthropic.add(ScriptedFailure(kind=ErrorKind.CREDENTIAL, status=status, message=message))

    run = await loop.loops.run(loop.owner, session_id)

    assert run.end is RunEnd.PARKED and run.park is not None
    assert (run.park.reason, run.park.unlock) == (ParkReason.PROVIDER, unlock)
    assert gate.credentials == [KEY], "the gate held the call knowing its key"
    assert keys.refusals == refused
    assert len(loop.anthropic.calls) == 1, "parked at once: no retry on the same key"
    for holder, credential in ((loop.owner.org_id, KEY), (SYSTEM_SCOPE, PLATFORM_CREDENTIAL)):
        assert await loop.outages.current(holder, "anthropic", credential) is None, credential
