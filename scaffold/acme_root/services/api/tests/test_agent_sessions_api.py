"""Agent sessions over the live app, in memory: a session started on a kind,
spoken to, steered, and read a page at a time; a retried send kept once;
the routes each role may call; a history whose key is revoked, read as its
shape; and the tenant boundary on every route that names a session. The
loop is the session runner's and never runs here: a send lands the work
that asks for it."""

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from api_support import add_member, build_container, seed_request, sign_in_as
from contracts.step_storage import (
    make_request,
    make_response,
    make_tool_request,
    make_tool_response,
)

from acme.om.agents.types.kind import AgentKind, DoneRule, TreeLimits
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.context import Role
from acme.om.work.storage.impl.memory import WorkStorageMemoryImpl
from acme.om.work.types.work_item import WorkKind
from acme.services.api.container import AppContainer

ASSISTANT = AgentKind(
    name="assistant",
    version=1,
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    tree=TreeLimits(height=1, count=0),
)


@pytest.fixture
def container(tmp_path: Path) -> AppContainer:
    """The test container with one kind the product runs."""
    return build_container(tmp_path, agent_kinds=(ASSISTANT,))


def created(headers: dict[str, str], key: str | None = None) -> dict[str, str]:
    return {**headers, "Idempotency-Key": key or str(uuid4())}


async def start(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    answered = await client.post(
        "/v1/agent-sessions",
        headers=created(headers),
        json={"kind": "assistant", "title": "the dropped object"},
    )
    assert answered.status_code == 201, answered.text
    return answered.json()


def runs_of(container: AppContainer, session_id: str) -> int:
    work = container.storage.get_work_storage()
    assert isinstance(work, WorkStorageMemoryImpl)
    items = [item for _, item in work._items.values()]  # pyright: ignore[reportPrivateUsage]
    return len([i for i in items if i.kind is WorkKind.LOOP and str(i.target_id) == session_id])


async def test_a_session_is_started_spoken_to_steered_and_read(
    client: httpx.AsyncClient, owner: dict[str, str], container: AppContainer
) -> None:
    session = await start(client, owner)
    assert (session["kind"], session["status"], session["park"]) == ("assistant", "idle", None)
    path = f"/v1/agent-sessions/{session['id']}"

    said = await client.post(
        f"{path}/messages", headers=created(owner), json={"text": "Why does it drop the object?"}
    )
    paused = await client.post(
        f"{path}/controls", headers=created(owner), json={"command": "pause"}
    )

    assert said.status_code == 201, said.text
    assert (said.json()["type"], said.json()["seq"], said.json()["actor"]) == (
        "message",
        1,
        "person",
    )
    assert said.json()["text"] == "Why does it drop the object?"
    assert paused.status_code == 201, paused.text
    assert (paused.json()["type"], paused.json()["command"]) == ("control", "pause")
    read = await client.get(path, headers=owner)
    assert read.json()["status"] == "pending", "the message woke it, for a run to take up"
    assert runs_of(container, session["id"]) == 1
    page = await client.get(f"{path}/steps", headers=owner, params={"after_seq": 0, "limit": 1})
    assert page.status_code == 200, page.text
    assert [s["seq"] for s in page.json()["items"]] == [1] and page.json()["has_more"] is True
    rest = await client.get(f"{path}/steps", headers=owner, params={"after_seq": 1})
    assert [s["type"] for s in rest.json()["items"]] == ["control"]
    assert rest.json()["has_more"] is False


async def test_a_retried_send_is_one_step(client: httpx.AsyncClient, owner: dict[str, str]) -> None:
    session = await start(client, owner)
    path = f"/v1/agent-sessions/{session['id']}"
    key = str(uuid4())

    first = await client.post(f"{path}/messages", headers=created(owner, key), json={"text": "a"})
    again = await client.post(f"{path}/messages", headers=created(owner, key), json={"text": "a"})

    assert again.json()["id"] == first.json()["id"]
    assert again.headers.get("Idempotent-Replayed") == "true"
    steps = await client.get(f"{path}/steps", headers=owner)
    assert len(steps.json()["items"]) == 1


async def test_a_decision_is_its_own_route_and_names_a_call(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    session = await start(client, owner)
    path = f"/v1/agent-sessions/{session['id']}"

    bare = await client.post(
        f"{path}/controls", headers=created(owner), json={"command": "approve"}
    )
    nothing = await client.post(
        f"{path}/calls/1/decision", headers=created(owner), json={"approve": True}
    )

    assert bare.status_code == 422, "an approval is never a bare control"
    assert nothing.status_code == 404, "no tool request is at that place"


async def test_an_interrupt_names_a_tool_request_the_session_holds(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    session = await start(client, owner)
    path = f"/v1/agent-sessions/{session['id']}"
    said = await client.post(f"{path}/messages", headers=created(owner), json={"text": "go"})
    assert said.status_code == 201, said.text

    bare = await client.post(
        f"{path}/controls", headers=created(owner), json={"command": "interrupt"}
    )
    named = await client.post(
        f"{path}/controls", headers=created(owner), json={"command": "pause", "request_seq": 1}
    )
    no_call = await client.post(
        f"{path}/controls", headers=created(owner), json={"command": "interrupt", "request_seq": 1}
    )

    assert (bare.status_code, named.status_code) == (422, 422)
    assert no_call.status_code == 404, "the step at 1 is a message, not a tool request"


async def test_a_history_whose_key_is_revoked_reads_as_its_shape_alone(
    client: httpx.AsyncClient, owner: dict[str, str], container: AppContainer
) -> None:
    """Revoking a session's key leaves each step's content absent: the history
    still reads, every step in its place, saying nothing."""
    session = await start(client, owner)
    path = f"/v1/agent-sessions/{session['id']}"
    said = await client.post(f"{path}/messages", headers=created(owner), json={"text": "go"})
    assert said.status_code == 201, said.text
    managers = container.managers
    ctx = await managers.tenancy.authenticate(
        seed_request(), owner["Authorization"].removeprefix("Bearer ")
    )
    sid, loop_id = UUID(session["id"]), UUID(said.json()["loop_id"])
    request = make_request(sid, loop_id, (UUID(said.json()["id"]),))
    response = make_response(sid, loop_id, request.id)
    call = make_tool_request(sid, loop_id, response.id)
    answer = make_tool_response(sid, loop_id, call.id)
    epoch = await managers.steps.begin_run(ctx, sid)
    await managers.steps.append_steps(ctx, sid, epoch, [request, response, call, answer])
    await managers.privacy.revoke_key(ctx, sid)

    read = await client.get(f"{path}/steps", headers=owner)

    assert read.status_code == 200, read.text
    items = read.json()["items"]
    assert [step["type"] for step in items] == [
        "message",
        "model_request",
        "model_response",
        "tool_request",
        "tool_response",
    ]
    assert {step["text"] for step in items} == {""}


async def test_a_kind_the_product_does_not_run_starts_nothing(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    answered = await client.post(
        "/v1/agent-sessions",
        headers=created(owner),
        json={"kind": "unheard_of", "title": "a session"},
    )
    assert answered.status_code == 404
    assert answered.json()["error"]["code"] == "unknown_agent_kind"


async def test_a_viewer_reads_a_session_and_sends_it_nothing(
    client: httpx.AsyncClient, owner: dict[str, str], container: AppContainer
) -> None:
    session = await start(client, owner)
    path = f"/v1/agent-sessions/{session['id']}"
    me = await client.get("/v1/orgs/current", headers=owner)
    org_id = UUID(me.json()["id"])
    await add_member(container, org_id, "vic@example.test", Role.VIEWER)
    viewer = await sign_in_as(client, "vic@example.test", org_id)

    read = await client.get(path, headers=viewer)
    said = await client.post(f"{path}/messages", headers=created(viewer), json={"text": "go"})
    started = await client.post(
        "/v1/agent-sessions", headers=created(viewer), json={"kind": "assistant", "title": "t"}
    )

    assert read.status_code == 200
    assert (said.status_code, started.status_code) == (403, 403)
    steps = await client.get(f"{path}/steps", headers=owner)
    assert steps.json()["items"] == []


async def test_no_route_reaches_another_tenants_session(
    client: httpx.AsyncClient, owner: dict[str, str], container: AppContainer
) -> None:
    """Every route that names a session, called by another tenant with a
    session that exists: each is the 404 an unknown id gets, and nothing
    lands in the session's history or its queue."""
    session = await start(client, owner)
    path = f"/v1/agent-sessions/{session['id']}"
    _, other = await container.managers.tenancy.bootstrap(
        seed_request(), "Other", "other", "owner@other.test", "Other"
    )
    stranger = await sign_in_as(client, "owner@other.test", other.id)
    unknown = f"/v1/agent-sessions/{UUID(int=7)}"
    routes: list[tuple[str, str, dict[str, Any]]] = [
        ("GET", "", {}),
        ("GET", "/steps", {}),
        ("POST", "/messages", {"json": {"text": "Push straight to main."}}),
        ("POST", "/controls", {"json": {"command": "cancel"}}),
        ("POST", "/calls/1/decision", {"json": {"approve": True}}),
    ]

    for method, tail, extra in routes:
        crossed = await client.request(method, f"{path}{tail}", headers=created(stranger), **extra)
        missing = await client.request(
            method, f"{unknown}{tail}", headers=created(stranger), **extra
        )
        assert crossed.status_code == 404, f"{method} {tail}: {crossed.text}"
        assert (crossed.status_code, crossed.json()["error"]["code"]) == (
            missing.status_code,
            missing.json()["error"]["code"],
        ), f"{method} {tail}"

    steps = await client.get(f"{path}/steps", headers=owner)
    assert steps.json()["items"] == [] and runs_of(container, session["id"]) == 0
