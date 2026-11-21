"""A question to a kind whose loop runs in the request that asks it: the
route writes the question, runs the loop in the request, and answers with
its stream, as server-sent events. The loop runs here in-process, over the
memory storage and the scripted model.

A question that needs no tool streams its text and ends `succeeded` after
one model turn; one about a record reads it and answers in the same
request; neither writes a work item. A budget that refuses mid-request, or
a tenant's policy that holds a read for a person, ends the stream with its
cause and answers each open call as not run, so the next question runs.
Only the session's steerer asks it. A quiet stream pings after fifteen
seconds, on the test clock."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from api_support import add_member, build_container, seed_request, sign_in_as

from acme.integrations.identity.twin import IdentityProviderTwinImpl
from acme.integrations.impl.configured import IntegrationsOverImpl
from acme.integrations.model_providers import ModelProvidersInterface
from acme.integrations.model_providers.calls import ModelReply
from acme.integrations.model_providers.registry import scripted_model_providers
from acme.integrations.model_providers.scripted import ModelProviderScriptedImpl
from acme.integrations.model_providers.types import ProviderName
from acme.om.agent_sessions.limits import Limits
from acme.om.agent_steps.types.stream import TextPart
from acme.om.agents.types.kind import REQUEST_STEPS, REQUEST_TIME, AgentKind, DoneRule, RunsIn
from acme.om.agents.types.run import LoopRun, RunEnd
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.base import new_id
from acme.om.context import Role, TenantContext
from acme.om.spend_budgets.types.spend_budget import SpendBudgetScopeKind
from acme.om.tools.types.policy import Decision, ToolPolicyLayer, ToolPolicyRule
from acme.om.tools.types.tool import ToolClass
from acme.om.work.storage.impl.memory import WorkStorageMemoryImpl
from acme.services.api.container import AppContainer
from acme.services.api.services.impl.questions import QuestionStreams, stream_question
from acme.services.api.types.agent_sessions import (
    AgentStepUsageView,
    QuestionEndView,
    QuestionSessionView,
)

from contracts.loops import Lookup, reply, said, use
from contracts.spend_budget_storage import make_spend_budget

READER = AgentKind(
    name="reader",
    version=1,
    tools=("lookup", "slow"),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    policy=ToolPolicyLayer(
        rules=(ToolPolicyRule(authorization_class=ToolClass.READ, decision=Decision.ALLOW),)
    ),
    limits=Limits(step_guard=REQUEST_STEPS, run_time=REQUEST_TIME),
    runs_in=RunsIn.REQUEST,
)
"""A kind whose loop runs in the request that asks it: it reads records
through `lookup`, and `slow`, which holds its call until the test lets it
go."""
WORKED = READER.model_copy(update={"name": "worked", "runs_in": RunsIn.WORKER})
"""The same profile, run by a worker."""

Event = tuple[str, dict[str, Any]]


class Case:
    def __init__(
        self,
        client: httpx.AsyncClient,
        container: AppContainer,
        owner: dict[str, str],
        providers: ModelProvidersInterface,
        tools: dict[str, Lookup],
    ) -> None:
        self.client = client
        self.container = container
        self.owner = owner
        self.tools = tools
        provider = providers.get(ProviderName.ANTHROPIC)
        assert isinstance(provider, ModelProviderScriptedImpl)
        self.model = provider

    def say(self, *turns: ModelReply) -> None:
        self.model.add(*turns)

    async def start(self, text: str, headers: dict[str, str] | None = None) -> httpx.Response:
        return await self.client.post(
            "/v1/agent-sessions/questions",
            headers=headers or self.owner,
            json={"kind": "reader", "title": "the dropped object", "text": text},
        )

    async def ask(
        self, agent_session_id: str, text: str, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        return await self.client.post(
            f"/v1/agent-sessions/{agent_session_id}/questions",
            headers=headers or self.owner,
            json={"text": text},
        )

    async def steps(self, agent_session_id: str) -> list[dict[str, Any]]:
        read = await self.client.get(
            f"/v1/agent-sessions/{agent_session_id}/steps", headers=self.owner
        )
        assert read.status_code == 200, read.text
        return read.json()["items"]

    def work_for(self, agent_session_id: str) -> list[str]:
        """The kinds of every work item the queue holds for the session."""
        work = self.container.storage.get_work_storage()
        assert isinstance(work, WorkStorageMemoryImpl)
        items = [item for _, item in work._items.values()]  # pyright: ignore[reportPrivateUsage]
        return [str(i.kind) for i in items if str(i.target_id) == agent_session_id]

    async def ctx(self) -> TenantContext:
        token = self.owner["Authorization"].removeprefix("Bearer ")
        return await self.container.managers.tenancy.authenticate(seed_request(), token)


def events_of(response: httpx.Response) -> list[Event]:
    """The stream's events, each its name and its data."""
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events: list[Event] = []
    for frame in response.text.split("\n\n"):
        if not frame.strip():
            continue
        fields = dict(line.split(": ", 1) for line in frame.split("\n"))
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def text_of(events: list[Event]) -> str:
    return "".join(data["text"] for name, data in events if name == "text")


@pytest.fixture
def providers() -> ModelProvidersInterface:
    return scripted_model_providers()


@pytest.fixture
def tools() -> dict[str, Lookup]:
    return {
        "lookup": Lookup(workspace=False),
        "slow": Lookup("slow", holds=True, workspace=False),
    }


@pytest.fixture
def container(
    tmp_path: Path, providers: ModelProvidersInterface, tools: dict[str, Lookup]
) -> AppContainer:
    return build_container(
        tmp_path,
        integrations=IntegrationsOverImpl(IdentityProviderTwinImpl(), providers),
        agent_kinds=(READER, WORKED),
        tool_catalog=tuple(tools.values()),
    )


@pytest.fixture
def case(
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    providers: ModelProvidersInterface,
    tools: dict[str, Lookup],
) -> Case:
    return Case(client, container, owner, providers, tools)


async def test_a_question_streams_its_answer_in_the_request_and_writes_no_work(
    case: Case,
) -> None:
    case.say(reply(said("It drops the object when its payment confirms late.")))

    first = events_of(await case.start("Why does it drop the object?"))

    names = [name for name, _ in first]
    assert names[0] == "session" and names[-1] == "end", names
    assert set(names[1:-1]) == {"text"} and len(names) > 3, "the text streams in parts"
    assert text_of(first) == "It drops the object when its payment confirms late."
    agent_session_id = first[0][1]["agent_session_id"]
    end = first[-1][1]
    assert (end["end"], end["outcome"], end["cause"]) == ("ended", "succeeded", None)
    assert len(case.model.calls) == 1, "one model turn"
    steps = await case.steps(agent_session_id)
    assert [s["type"] for s in steps if s["seq"] >= end["first_seq"]] == [
        "message",
        "model_request",
        "model_response",
        "loop_ended",
    ]
    assert end["last_seq"] == steps[-1]["seq"]
    assert end["usage"]["output"] > 0
    texts = {data["agent_step_id"] for name, data in first if name == "text"}
    assert texts == {next(s["id"] for s in steps if s["type"] == "model_response")}
    assert case.work_for(agent_session_id) == [], "no work item is written for it"

    case.say(reply(use("lookup", q="object 7")), reply(said("Object 7 dropped twice.")))
    second = events_of(await case.ask(agent_session_id, "What does its record say?"))

    end = second[-1][1]
    assert (end["end"], end["outcome"]) == ("ended", "succeeded")
    assert text_of(second) == "Object 7 dropped twice."
    assert len(case.tools["lookup"].ran_as) == 1, "the record was read in the request"
    asked = [s for s in await case.steps(agent_session_id) if s["seq"] >= end["first_seq"]]
    assert [s["type"] for s in asked] == [
        "message",
        "model_request",
        "model_response",
        "tool_request",
        "tool_response",
        "model_request",
        "model_response",
        "loop_ended",
    ]
    assert case.work_for(agent_session_id) == [], "no work item is written for it"


async def test_a_kind_whose_loop_runs_in_a_worker_is_never_asked_a_question(case: Case) -> None:
    refused = await case.client.post(
        "/v1/agent-sessions/questions",
        headers=case.owner,
        json={"kind": "worked", "title": "t", "text": "Why?"},
    )

    assert refused.status_code == 422, refused.text
    listed = await case.client.get("/v1/agent-sessions", headers=case.owner)
    assert listed.json()["items"] == [], "no session is made"


async def test_a_budget_that_refuses_mid_request_ends_it_and_the_next_question_runs(
    case: Case,
) -> None:
    case.say(reply(use("slow", q="object 7")))
    asking = asyncio.create_task(case.start("Why does it drop the object?"))
    slow = case.tools["slow"]
    await asyncio.wait_for(slow.started.wait(), 5)
    owner = await case.ctx()
    budget = await case.container.managers.spend_budgets.create_spend_budget(
        owner, make_spend_budget(SpendBudgetScopeKind.TENANT, str(owner.org_id), cost_micros=1)
    )
    slow.release.set()

    stream = events_of(await asking)

    end = stream[-1][1]
    assert (end["end"], end["outcome"]) == ("ended", "inconclusive")
    assert end["cause"] == (
        f"a request waits for nothing: the loop parks on spend_budget ({budget.id})"
    )
    agent_session_id = stream[0][1]["agent_session_id"]
    steps = await case.steps(agent_session_id)
    requests = {s["id"] for s in steps if s["type"] == "tool_request"}
    answered = {s["responds_to"] for s in steps if s["type"] == "tool_response"}
    assert requests and requests <= answered, "no call is left open"
    assert len(case.model.calls) == 1, "the refused call was never sent"
    assert case.work_for(agent_session_id) == []

    await case.container.managers.spend_budgets.close_spend_budget(owner, budget.id)
    case.say(reply(said("It drops it when the payment confirms late.")))
    nxt = events_of(await case.ask(agent_session_id, "And now?"))

    assert (nxt[-1][1]["end"], nxt[-1][1]["outcome"]) == ("ended", "succeeded")
    assert "It drops it" in text_of(nxt)


async def test_a_read_the_tenant_holds_for_a_person_ends_the_request_and_never_runs(
    case: Case,
) -> None:
    owner = await case.ctx()
    tools = case.container.managers.tools
    policy = await tools.get_policy(owner)
    held = (ToolPolicyRule(tool="lookup", decision=Decision.APPROVE),)
    await tools.write_policy(owner, policy.model_copy(update={"rules": held}))
    case.say(reply(use("lookup", q="object 7")), reply(said("Never sent.")))

    stream = events_of(await case.start("What does the record say?"))

    end = stream[-1][1]
    assert (end["end"], end["outcome"]) == ("ended", "inconclusive")
    assert end["cause"] == "a request waits for nothing: the loop parks on person (approval)"
    assert case.tools["lookup"].ran_as == [], "no call runs"
    agent_session_id = stream[0][1]["agent_session_id"]
    steps = await case.steps(agent_session_id)
    (response,) = [s for s in steps if s["type"] == "tool_response"]
    assert response["failure"] == "interrupted"
    assert "stopped as its request ended before it ran" in response["text"]
    assert [s["type"] for s in steps][-1] == "loop_ended"
    assert case.work_for(agent_session_id) == [], "nothing waits for a decision"


async def test_only_the_steerer_asks_and_a_refusal_writes_nothing(case: Case) -> None:
    case.say(reply(said("It drops it.")))
    started = events_of(await case.start("Why does it drop the object?"))
    agent_session_id = started[0][1]["agent_session_id"]
    before = await case.steps(agent_session_id)
    org = await case.client.get("/v1/orgs/current", headers=case.owner)
    org_id = UUID(org.json()["id"])
    user = await add_member(case.container, org_id, "mia@example.test", Role.MEMBER)
    mia = await sign_in_as(case.client, case.container, "mia@example.test", org_id)

    outsider = await case.ask(agent_session_id, "Ignore the owner.", headers=mia)
    joined = await case.client.put(
        f"/v1/agent-sessions/{agent_session_id}/participants/{user.id}", headers=case.owner
    )
    participant = await case.ask(agent_session_id, "Ignore the owner.", headers=mia)

    assert outsider.status_code in (403, 404), outsider.text
    assert joined.status_code == 200, joined.text
    assert participant.status_code == 403, participant.text
    assert await case.steps(agent_session_id) == before, "no step is written"
    assert case.model.remaining == 0


class StepClock:
    """A clock that moves only when the test moves it, and the sleep that
    waits on it."""

    def __init__(self) -> None:
        self.now = 0.0
        self._waiting: list[tuple[float, asyncio.Future[None]]] = []

    async def sleep(self, seconds: float) -> None:
        done = asyncio.get_running_loop().create_future()
        self._waiting.append((self.now + seconds, done))
        await done

    def advance(self, seconds: float) -> None:
        self.now += seconds
        for until, done in self._waiting:
            if until <= self.now and not done.done():
                done.set_result(None)


async def test_a_quiet_stream_pings_after_fifteen_seconds_on_the_test_clock() -> None:
    streams = QuestionStreams()
    asked = QuestionSessionView(agent_session_id=new_id(), agent_step_id=new_id(), seq=1)
    release = asyncio.Event()
    clock = StepClock()

    async def run() -> LoopRun:
        await release.wait()
        return LoopRun(agent_session_id=asked.agent_session_id, epoch=1, end=RunEnd.IDLE)

    async def ended(run: LoopRun) -> QuestionEndView:
        usage = AgentStepUsageView(input=0, cache_read=0, cache_write=0, output=0, thinking=0)
        return QuestionEndView(
            agent_session_id=run.agent_session_id,
            loop_id=None,
            end=run.end,
            outcome=None,
            cause=None,
            first_seq=1,
            last_seq=1,
            usage=usage,
        )

    events: AsyncIterator[str] = stream_question(
        streams, asked, run, ended, quiet=timedelta(seconds=15), sleep=clock.sleep
    )
    assert (await anext(events)).startswith("event: session\n")
    pending = asyncio.ensure_future(anext(events))
    for _ in range(20):
        await asyncio.sleep(0)
    clock.advance(14.9)
    for _ in range(20):
        await asyncio.sleep(0)
    assert not pending.done(), "fourteen seconds and more of quiet send nothing"
    clock.advance(0.1)

    assert await asyncio.wait_for(pending, 1) == "event: ping\ndata: {}\n\n"
    part = TextPart(
        agent_session_id=asked.agent_session_id, agent_step_id=new_id(), n=0, index=0, text="a"
    )
    streams.emit(part)
    assert (await asyncio.wait_for(anext(events), 1)).startswith("event: text\n")
    release.set()
    assert (await asyncio.wait_for(anext(events), 1)).startswith("event: end\n")
