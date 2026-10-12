"""Every park a person clears, cleared through the API by the person who
may clear it, and the session runs again: a loop runs here in-process,
over the memory storage and the scripted model, and parks the way it
does in use. A question is answered by a message, an approval by a
decision, a passed deadline by a later one, a principal who no longer
holds by an assignment, a hand-over by its holder's giving back, a job by
its completion, and the step guard, a spender, and a lost workspace by an
unlock. Each route answers the session pending, its park gone, with the
work that runs it queued, and the next run goes on to its end.

A hand-over is its holder's alone: no other member, and no program on an
API key, the holder's own included, unlocks it or gives it back. A
cancel still ends it."""

from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from api_support import add_member, build_container, seed_request, sign_in_as
from contracts.loops import Build, Lookup, call, reply, said, use

from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.integrations.impl.configured import IntegrationsOverImpl
from acme.integrations.model_providers import ModelProvidersInterface
from acme.integrations.model_providers.calls import ModelReply
from acme.integrations.model_providers.registry import scripted_model_providers
from acme.integrations.model_providers.scripted import ModelProviderScriptedImpl
from acme.integrations.model_providers.types import ProviderName
from acme.om.agent_sessions.limits import Limits
from acme.om.agents.types.kind import AgentKind, DoneRule, TreeLimits
from acme.om.agents.types.run import RunEnd
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.base import new_id, utcnow
from acme.om.context import Role, TenantContext
from acme.om.steps.types.header import LoopOutcome, Park, ParkReason
from acme.om.tools.types.policy import Decision, PolicyLayer, PolicyRule
from acme.om.tools.types.tool import ToolClass
from acme.om.work.storage.impl.memory import WorkStorageMemoryImpl
from acme.om.work.types.work_item import WorkKind
from acme.services.api.container import AppContainer

POLICY = PolicyLayer(
    rules=(
        PolicyRule(authorization_class=ToolClass.READ, decision=Decision.ALLOW),
        PolicyRule(authorization_class=ToolClass.WRITE, decision=Decision.ALLOW),
        PolicyRule(tool="note", decision=Decision.APPROVE),
    )
)
STEWARD = AgentKind(
    name="steward",
    version=1,
    tools=("lookup", "note", "build", "ask_person"),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.STEADY,
    tree=TreeLimits(height=1, count=0),
    policy=POLICY,
)
"""A steady kind, so its calls run under one principal a person may have to
replace; `note` waits for a person's approval, and `build` is a job."""
GUARDED = STEWARD.model_copy(update={"name": "guarded", "limits": Limits(step_guard=1)})
"""A kind whose step guard trips at its second model call in a loop."""


class Case:
    """A container whose loop calls the scripted model, and a person signed
    in on it: the owner, Ann."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        container: AppContainer,
        owner: dict[str, str],
        providers: ModelProvidersInterface,
    ) -> None:
        self.client = client
        self.container = container
        self.owner = owner
        provider = providers.get(ProviderName.ANTHROPIC)
        assert isinstance(provider, ModelProviderScriptedImpl)
        self.model = provider

    def say(self, *turns: ModelReply) -> None:
        self.model.add(*turns)

    async def started(self, headers: dict[str, str], kind: str = "steward") -> str:
        """A session on `kind`, spoken to: its loop waits for a run."""
        made = await self.client.post(
            "/v1/agent-sessions", headers=created(headers), json={"kind": kind, "title": "t"}
        )
        assert made.status_code == 201, made.text
        session_id = made.json()["id"]
        said_ = await self.client.post(
            f"/v1/agent-sessions/{session_id}/messages",
            headers=created(headers),
            json={"text": "Count the dropped objects."},
        )
        assert said_.status_code == 201, said_.text
        return session_id

    async def ctx(self, headers: dict[str, str] | None = None) -> TenantContext:
        token = (headers or self.owner)["Authorization"].removeprefix("Bearer ")
        return await self.container.managers.tenancy.authenticate(seed_request(), token)

    async def run(self, session_id: str) -> Any:
        return await self.container.managers.loop.run(await self.ctx(), UUID(session_id))

    async def parked_on(self, session_id: str) -> dict[str, Any]:
        """The park the session waits on, as the API reads it."""
        read = await self.client.get(f"/v1/agent-sessions/{session_id}", headers=self.owner)
        assert read.status_code == 200, read.text
        assert read.json()["status"] == "parked", read.text
        return read.json()["park"]

    async def steps(self, session_id: str) -> list[dict[str, Any]]:
        read = await self.client.get(f"/v1/agent-sessions/{session_id}/steps", headers=self.owner)
        assert read.status_code == 200, read.text
        return read.json()["items"]

    def runs_of(self, session_id: str) -> int:
        work = self.container.storage.get_work_storage()
        assert isinstance(work, WorkStorageMemoryImpl)
        items = [item for _, item in work._items.values()]  # pyright: ignore[reportPrivateUsage]
        return len([i for i in items if i.kind is WorkKind.LOOP and str(i.target_id) == session_id])

    async def runs_again(self, session_id: str, cleared: httpx.Response, runs: int) -> None:
        """The route answered the session pending, its park gone, with one
        more run asked for; the next run goes on to the loop's end, and the
        model's last turn was read."""
        assert cleared.status_code in (200, 201), cleared.text
        session = await self.client.get(f"/v1/agent-sessions/{session_id}", headers=self.owner)
        assert (session.json()["status"], session.json()["park"]) == ("pending", None)
        assert self.runs_of(session_id) == runs + 1, "the work that runs the session is queued"
        run = await self.run(session_id)
        assert (run.end, run.outcome) == (RunEnd.ENDED, LoopOutcome.SUCCEEDED), run
        assert self.model.remaining == 0


def created(headers: dict[str, str]) -> dict[str, str]:
    return {**headers, "Idempotency-Key": str(uuid4())}


@pytest.fixture
def providers() -> ModelProvidersInterface:
    return scripted_model_providers()


@pytest.fixture
def build() -> Build:
    return Build()


@pytest.fixture
def container(tmp_path: Path, providers: ModelProvidersInterface, build: Build) -> AppContainer:
    integrations = IntegrationsOverImpl(IdentityProviderAbsentImpl(), providers)
    return build_container(
        tmp_path,
        integrations=integrations,
        agent_kinds=(STEWARD, GUARDED),
        tool_catalog=(Lookup(), Lookup("note", authorization_class=ToolClass.WRITE), build),
    )


@pytest.fixture
def case(
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    providers: ModelProvidersInterface,
) -> Case:
    return Case(client, container, owner, providers)


async def org_of(case: Case) -> UUID:
    me = await case.client.get("/v1/orgs/current", headers=case.owner)
    return UUID(me.json()["id"])


async def member(case: Case, email: str = "mia@example.test") -> tuple[UUID, dict[str, str]]:
    org_id = await org_of(case)
    user = await add_member(case.container, org_id, email, Role.MEMBER)
    return user.id, await sign_in_as(case.client, email, org_id)


async def api_key_of(case: Case, headers: dict[str, str]) -> dict[str, str]:
    """A key the person mints for a program, which speaks in their name."""
    key = await case.client.post(
        "/v1/api-keys", headers=created(headers), json={"name": "ci", "role": "member"}
    )
    assert key.status_code == 201, key.text
    return {"Authorization": f"Bearer {key.json()['key']}"}


def request_seq(steps: Sequence[dict[str, Any]], tool: str) -> int:
    return next(s["seq"] for s in steps if s["type"] == "tool_request" and s["tool"] == tool)


# A question, an approval: their routes answer what the park waits on.


async def test_a_question_is_answered_by_a_message(case: Case) -> None:
    session_id = await case.started(case.owner)
    case.say(reply(call("ask_person", question="Which shelf?")))
    await case.run(session_id)
    assert (await case.parked_on(session_id))["unlock"] == "answer"
    runs = case.runs_of(session_id)
    case.say(reply(said("Shelf two holds three.")))

    answered = await case.client.post(
        f"/v1/agent-sessions/{session_id}/messages",
        headers=created(case.owner),
        json={"text": "The second shelf."},
    )

    await case.runs_again(session_id, answered, runs)


async def test_an_approval_is_cleared_by_a_decision(case: Case) -> None:
    session_id = await case.started(case.owner)
    case.say(reply(use("note")))
    await case.run(session_id)
    assert (await case.parked_on(session_id))["unlock"] == "approval"
    seq = request_seq(await case.steps(session_id), "note")
    runs = case.runs_of(session_id)
    case.say(reply(said("Noted.")))

    decided = await case.client.post(
        f"/v1/agent-sessions/{session_id}/calls/{seq}/decision",
        headers=created(case.owner),
        json={"approve": True},
    )

    await case.runs_again(session_id, decided, runs)


# A passed deadline, a principal who no longer holds, a hand-over, a job:
# each has its own route.


async def test_a_passed_deadline_is_cleared_by_a_later_one(case: Case) -> None:
    session_id = await case.started(case.owner)
    path = f"/v1/agent-sessions/{session_id}/deadline"
    past = await case.client.put(
        path, headers=case.owner, json={"deadline": (utcnow() - timedelta(minutes=1)).isoformat()}
    )
    assert past.status_code == 200, past.text
    await case.run(session_id)
    assert (await case.parked_on(session_id))["unlock"] == "deadline"
    runs = case.runs_of(session_id)
    case.say(reply(said("Three dropped.")))

    moved = await case.client.put(
        path, headers=case.owner, json={"deadline": (utcnow() + timedelta(hours=1)).isoformat()}
    )

    await case.runs_again(session_id, moved, runs)


async def test_a_deadline_with_no_offset_is_refused_and_the_one_set_stays(case: Case) -> None:
    """An instant with no offset names no moment: the route refuses it
    before anything is written, and the tree keeps the deadline it had."""
    session_id = await case.started(case.owner)
    path = f"/v1/agent-sessions/{session_id}/deadline"
    later = utcnow() + timedelta(hours=1)
    kept = await case.client.put(path, headers=case.owner, json={"deadline": later.isoformat()})
    assert kept.status_code == 200, kept.text

    naive = await case.client.put(
        path, headers=case.owner, json={"deadline": "2099-01-01T09:00:00"}
    )

    assert naive.status_code == 422, naive.text
    tree = await case.container.managers.agents.tree_of(await case.ctx(), UUID(session_id))
    assert tree.deadline == later


async def test_a_principal_who_no_longer_holds_is_replaced_by_the_person_who_assigns_themselves(
    case: Case,
) -> None:
    """A steady session runs its calls under the member who made it. She
    leaves the org, so its call parks for a person; the owner assigns
    herself, and the call runs under her."""
    mia, as_mia = await member(case)
    session_id = await case.started(as_mia)
    gone = await case.client.delete(f"/v1/memberships/{mia}", headers=case.owner)
    assert gone.status_code == 200, gone.text
    case.say(reply(use("lookup")))
    await case.run(session_id)
    assert (await case.parked_on(session_id))["unlock"] == "principal"
    runs = case.runs_of(session_id)
    case.say(reply(said("Three dropped.")))

    assigned = await case.client.put(
        f"/v1/agent-sessions/{session_id}/principal", headers=case.owner
    )

    await case.runs_again(session_id, assigned, runs)
    responses = [s for s in await case.steps(session_id) if s["type"] == "tool_response"]
    assert [s["failure"] for s in responses] == [None], "the call ran under the new principal"


async def test_a_hand_over_is_cleared_by_its_holder_giving_it_back(case: Case) -> None:
    session_id = await case.started(case.owner)
    path = f"/v1/agent-sessions/{session_id}"
    taken = await case.client.post(f"{path}/take-over", headers=case.owner)
    assert taken.status_code == 200, taken.text
    park = await case.parked_on(session_id)
    ann = (await case.ctx()).user_id
    assert (park["reason"], park["holder"]) == ("handover", str(ann))
    runs = case.runs_of(session_id)
    case.say(reply(said("Three dropped, and the shelf is fixed.")))

    given = await case.client.post(
        f"{path}/give-back",
        headers=created(case.owner),
        json={"summary": "I fixed the shelf by hand."},
    )

    await case.runs_again(session_id, given, runs)


async def test_a_job_is_cleared_by_its_completion(case: Case, build: Build) -> None:
    session_id = await case.started(case.owner)
    case.say(reply(use("build")))
    await case.run(session_id)
    park = await case.parked_on(session_id)
    assert park["reason"] == "job"
    handle = build.started[UUID(park["unlock"])]
    runs = case.runs_of(session_id)
    case.say(reply(said("The build passed.")))

    completed = await case.client.post(
        f"/v1/agent-sessions/{session_id}/jobs/{park['unlock']}/completion",
        headers=created(case.owner),
        json={"handle": handle, "text": "passed"},
    )

    await case.runs_again(session_id, completed, runs)


# The step guard, a spender, a lost workspace: a person who looked unlocks.


async def test_the_step_guard_is_cleared_by_an_unlock(case: Case) -> None:
    session_id = await case.started(case.owner, kind="guarded")
    case.say(reply(use("lookup")))
    await case.run(session_id)
    assert (await case.parked_on(session_id))["unlock"] == "step_guard"
    runs = case.runs_of(session_id)
    case.say(reply(said("Three dropped.")))

    unlocked = await case.client.post(
        f"/v1/agent-sessions/{session_id}/controls",
        headers=created(case.owner),
        json={"command": "unlock"},
    )

    await case.runs_again(session_id, unlocked, runs)


@pytest.mark.parametrize("unlock", ["spender", "workspace"])
async def test_a_spender_or_a_lost_workspace_is_cleared_by_an_unlock(
    case: Case, unlock: str
) -> None:
    """Parks whose cause the API cannot bring about, a gate that cannot tell
    who pays or a snapshot gone from the store, written as the loop writes
    them."""
    session_id = await case.started(case.owner)
    ctx = await case.ctx()
    epoch = await case.container.managers.steps.begin_run(ctx, UUID(session_id))
    park = Park(reason=ParkReason.PERSON, unlock=unlock)
    await case.container.managers.agent_sessions.park(ctx, UUID(session_id), epoch, new_id(), park)
    assert (await case.parked_on(session_id))["unlock"] == unlock
    runs = case.runs_of(session_id)
    case.say(reply(said("Three dropped.")))

    unlocked = await case.client.post(
        f"/v1/agent-sessions/{session_id}/controls",
        headers=created(case.owner),
        json={"command": "unlock"},
    )

    await case.runs_again(session_id, unlocked, runs)


# A hand-over is its holder's.


async def test_a_hand_over_is_unlocked_by_its_holder_alone_in_person(case: Case) -> None:
    """Another member, a program on another member's key, and a program on
    the holder's own key may write, and none of them unlocks the hand-over
    or gives it back: each is refused, and the environment stays with its
    holder. Her own unlock, in person, clears it."""
    session_id = await case.started(case.owner)
    path = f"/v1/agent-sessions/{session_id}"
    _, as_mia = await member(case)
    others = {
        "another member": as_mia,
        "another member's key": await api_key_of(case, as_mia),
        "the holder's own key": await api_key_of(case, case.owner),
    }
    taken = await case.client.post(f"{path}/take-over", headers=case.owner)
    assert taken.status_code == 200, taken.text
    held = await case.parked_on(session_id)

    for who, headers in others.items():
        unlocked = await case.client.post(
            f"{path}/controls", headers=created(headers), json={"command": "unlock"}
        )
        given = await case.client.post(
            f"{path}/give-back", headers=created(headers), json={"summary": "Done."}
        )
        assert (unlocked.status_code, given.status_code) == (403, 403), who
        assert await case.parked_on(session_id) == held, who
    retaken = await case.client.post(f"{path}/take-over", headers=as_mia)
    by_key = await case.client.post(f"{path}/take-over", headers=others["the holder's own key"])
    assert (retaken.status_code, by_key.status_code) == (409, 403)
    assert await case.parked_on(session_id) == held
    assert [s for s in await case.steps(session_id) if s["type"] == "control"] == []

    unlocked = await case.client.post(
        f"{path}/controls", headers=created(case.owner), json={"command": "unlock"}
    )

    assert unlocked.status_code == 201, unlocked.text
    session = await case.client.get(path, headers=case.owner)
    assert (session.json()["status"], session.json()["park"]) == ("pending", None)


async def test_a_cancel_by_another_member_still_ends_a_held_hand_over(case: Case) -> None:
    session_id = await case.started(case.owner)
    path = f"/v1/agent-sessions/{session_id}"
    _, as_mia = await member(case)
    await case.client.post(f"{path}/take-over", headers=case.owner)

    cancelled = await case.client.post(
        f"{path}/controls", headers=created(as_mia), json={"command": "cancel"}
    )

    assert cancelled.status_code == 201, cancelled.text
    session = await case.client.get(path, headers=case.owner)
    assert (session.json()["status"], session.json()["park"]) == ("pending", None)
