"""Who sees a session, who takes part in it, and who steers it, over the
live app in memory.

A session is org-wide or private: a steady kind's are org-wide and a
delegated kind's private, until its creator switches it, a recorded step.
Its history and its tool outputs open to whom it is visible: every member of
the org when it is org-wide; its creator, its participants, and a holder
of `manage_agent_sessions` when it is private. To anyone else it answers as one
that never existed. One person steers it at a time: its creator, until a
recorded hand-off to a participant, and a holder of `manage_agent_sessions`
takes over one whose steerer lapsed."""

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from api_support import add_member, build_container, seed_request, sign_in_as
from contracts.histories import History

from acme.om.agent_steps.types.header import ToolResponseHeader
from acme.om.agents.types.kind import AgentKind, DoneRule, TreeLimits
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.context import Role
from acme.services.api.container import AppContainer

ASSISTANT = AgentKind(
    name="assistant",
    version=1,
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    tree=TreeLimits(height=1, count=0),
)
"""A delegated kind: its tools run with one person's own permissions."""
STEWARD = ASSISTANT.model_copy(update={"name": "steward", "authority": AuthorityMode.STEADY})
"""A steady kind: it behaves the same for whoever resumes it."""


@pytest.fixture
def container(tmp_path: Path) -> AppContainer:
    return build_container(tmp_path, agent_kinds=(ASSISTANT, STEWARD))


class Org:
    """The owner, Ann, who starts the sessions; two members, Mia and Noa;
    and an admin, Ada, who holds `manage_agent_sessions`."""

    def __init__(self, client: httpx.AsyncClient, container: AppContainer) -> None:
        self.client = client
        self.container = container
        self.ids: dict[str, UUID] = {}
        self.headers: dict[str, dict[str, str]] = {}

    async def seat(self, owner: dict[str, str]) -> None:
        me = await self.client.get("/v1/me", headers=owner)
        org_id = UUID(me.json()["org"]["id"])
        self.ids["ann"], self.headers["ann"] = UUID(me.json()["user"]["id"]), owner
        for name, role in (("mia", Role.MEMBER), ("noa", Role.MEMBER), ("ada", Role.ADMIN)):
            email = f"{name}@example.test"
            self.ids[name] = (await add_member(self.container, org_id, email, role)).id
            self.headers[name] = await sign_in_as(self.client, email, org_id)

    async def start(self, kind: str, by: str = "ann") -> str:
        made = await self.client.post(
            "/v1/agent-sessions",
            headers=created(self.headers[by]),
            json={"kind": kind, "title": "the dropped order"},
        )
        assert made.status_code == 201, made.text
        agent_session_id = made.json()["id"]
        said = await self.client.post(
            f"/v1/agent-sessions/{agent_session_id}/messages",
            headers=created(self.headers[by]),
            json={"text": "Find out why an order drops."},
        )
        assert said.status_code == 201, said.text
        return agent_session_id

    async def keep_tool_output(self, agent_session_id: str) -> str:
        """A tool's result past the size bound, kept whole as a tool output
        of the session, as the loop keeps it."""
        token = self.headers["ann"]["Authorization"].removeprefix("Bearer ")
        ctx = await self.container.managers.tenancy.authenticate(seed_request(), token)
        history = History(UUID(agent_session_id))
        objective = history.message("Read the whole log.")
        asked = history.response(history.request((objective,)), "", [("c1", "read_log", {})])
        text = "".join(f"a line of the log {n:07d}\n" for n in range(1_200))
        result = history.result(history.call(asked, "c1"), text)
        bounded = await self.container.managers.windows.bound_tool_response(
            ctx, UUID(agent_session_id), result
        )
        assert isinstance(bounded.header, ToolResponseHeader) and bounded.header.tool_output
        return str(bounded.header.tool_output.id)

    async def reads(
        self, who: str, agent_session_id: str, tool_output_id: str
    ) -> tuple[int, int, int]:
        """What `who` gets for the session, its history, and its tool output."""
        path = f"/v1/agent-sessions/{agent_session_id}"
        headers = self.headers[who]
        session = await self.client.get(path, headers=headers)
        steps = await self.client.get(f"{path}/steps", headers=headers)
        tool_output = await self.client.get(
            f"{path}/tool-outputs/{tool_output_id}", headers=headers
        )
        return session.status_code, steps.status_code, tool_output.status_code

    async def put(self, who: str, path: str, body: dict[str, Any] | None = None) -> httpx.Response:
        return await self.client.put(path, headers=self.headers[who], json=body)

    async def delete(self, who: str, path: str) -> httpx.Response:
        return await self.client.delete(path, headers=self.headers[who])

    async def participants(self, agent_session_id: str) -> list[str]:
        """Who takes part in the session, as its creator reads it."""
        read = await self.client.get(
            f"/v1/agent-sessions/{agent_session_id}", headers=self.headers["ann"]
        )
        assert read.status_code == 200, read.text
        return read.json()["participants"]

    async def listed(self, who: str, among: str) -> list[str]:
        page = await self.client.get(
            "/v1/agent-sessions", headers=self.headers[who], params={"among": among}
        )
        assert page.status_code == 200, page.text
        return [item["id"] for item in page.json()["items"]]

    async def steps(self, agent_session_id: str) -> list[dict[str, Any]]:
        read = await self.client.get(
            f"/v1/agent-sessions/{agent_session_id}/steps", headers=self.headers["ann"]
        )
        assert read.status_code == 200, read.text
        return read.json()["items"]


def created(headers: dict[str, str]) -> dict[str, str]:
    return {**headers, "Idempotency-Key": str(uuid4())}


@pytest.fixture
async def org(client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]) -> Org:
    seated = Org(client, container)
    await seated.seat(owner)
    return seated


async def test_a_sessions_history_and_tool_outputs_open_to_whom_it_is_visible(org: Org) -> None:
    """An org-wide session's history and tool outputs are served to a member
    who did not make it. A private one's are refused to a member who is
    neither its creator nor a participant, as one that never existed, and
    served to a participant and to an admin, who holds `manage_agent_sessions`."""
    open_id = await org.start("steward")
    open_tool_output = await org.keep_tool_output(open_id)
    closed_id = await org.start("assistant")
    closed_tool_output = await org.keep_tool_output(closed_id)
    added = await org.put("ann", f"/v1/agent-sessions/{closed_id}/participants/{org.ids['noa']}")
    assert added.status_code == 200, added.text

    assert await org.reads("mia", open_id, open_tool_output) == (200, 200, 200)
    assert await org.reads("mia", closed_id, closed_tool_output) == (404, 404, 404)
    assert await org.reads("noa", closed_id, closed_tool_output) == (200, 200, 200)
    assert await org.reads("ada", closed_id, closed_tool_output) == (200, 200, 200)
    assert await org.listed("mia", "visible") == [open_id]
    assert await org.listed("noa", "mine") == [closed_id]
    assert await org.listed("ada", "visible") == [open_id, closed_id]
    joined = await org.put("mia", f"/v1/agent-sessions/{closed_id}/participants/{org.ids['mia']}")
    assert joined.status_code == 404, "a private session is joined by whom its creator adds"


async def test_a_sessions_visibility_follows_its_kind_until_its_creator_switches_it(
    org: Org,
) -> None:
    """A new session of a delegated kind is private, and one of a steady
    kind org-wide. Its creator switches it, a `switched` step that names
    the visibility; a switch to what it holds already records nothing, and
    anyone else's is refused."""
    delegated = await org.start("assistant")
    steady = await org.start("steward")
    path = f"/v1/agent-sessions/{steady}/visibility"

    seen = [
        (await org.client.get(f"/v1/agent-sessions/{sid}", headers=org.headers["ann"])).json()
        for sid in (delegated, steady)
    ]
    by_another = await org.put("mia", path, {"visibility": "private"})
    switched = await org.put("ann", path, {"visibility": "private"})
    again = await org.put("ann", path, {"visibility": "private"})

    assert [s["visibility"] for s in seen] == ["private", "org_wide"]
    assert by_another.status_code == 403
    assert (switched.status_code, switched.json()["visibility"]) == (200, "private")
    assert again.status_code == 200
    recorded = [s for s in await org.steps(steady) if s["type"] == "switched"]
    assert [(s["visibility"], s["actor"]) for s in recorded] == [("private", "person")]
    gone = await org.client.get(f"/v1/agent-sessions/{steady}", headers=org.headers["mia"])
    assert gone.status_code == 404, "switched private, it is no longer the member's to see"


async def test_one_steerer_at_a_time_hands_the_steer_on_and_an_admin_takes_a_lapsed_one(
    org: Org,
) -> None:
    """The creator steers until they hand the steer to a participant: the
    move is recorded, the participant's messages instruct from then on, and
    the creator's are data. Nobody else hands it on, nor takes it without
    `manage_agent_sessions`. Its steerer leaves the org, and an admin takes the
    session over through the route that assigns a principal."""
    sid = await org.start("steward")
    path = f"/v1/agent-sessions/{sid}"
    mia, noa = str(org.ids["mia"]), str(org.ids["noa"])
    await org.put("mia", f"{path}/participants/{mia}")

    by_another = await org.put("noa", f"{path}/principal", {"steerer": mia})
    taken_unheld = await org.put("mia", f"{path}/principal")
    to_an_outsider = await org.put("ann", f"{path}/principal", {"steerer": noa})
    handed = await org.put("ann", f"{path}/principal", {"steerer": mia})
    by_the_last_steerer = await org.put("ann", f"{path}/principal", {"steerer": mia})
    hers = await org.client.post(
        f"{path}/messages", headers=created(org.headers["mia"]), json={"text": "Check refunds."}
    )
    his = await org.client.post(
        f"{path}/messages", headers=created(org.headers["ann"]), json={"text": "And retries."}
    )

    assert (by_another.status_code, taken_unheld.status_code) == (403, 403)
    assert to_an_outsider.status_code == 422, to_an_outsider.text
    assert (handed.status_code, handed.json()["steerer"]) == (200, mia)
    assert by_the_last_steerer.status_code == 403, "the steer is no longer the creator's"
    assert (hers.json()["participant"], his.json()["participant"]) == (False, True)
    authority = org.container.managers.attribution
    token = org.headers["ann"]["Authorization"].removeprefix("Bearer ")
    ctx = await org.container.managers.tenancy.authenticate(seed_request(), token)
    held = await authority.get_authority(ctx, UUID(sid))
    assert held.principal == Principal(kind=PrincipalKind.PERSON, id=org.ids["mia"])

    left = await org.client.delete(f"/v1/memberships/{mia}", headers=org.headers["ann"])
    assert left.status_code == 200, left.text
    by_a_member = await org.put("noa", f"{path}/principal")
    taken = await org.put("ada", f"{path}/principal")

    assert by_a_member.status_code == 403
    assert (taken.status_code, taken.json()["steerer"]) == (200, str(org.ids["ada"]))
    assert str(org.ids["ada"]) in taken.json()["participants"], "who takes it takes part"
    moves = [s["steerer"] for s in await org.steps(sid) if s["type"] == "switched"]
    assert moves == [mia, str(org.ids["ada"])], "each move recorded once"


async def test_an_admin_takes_no_steer_from_a_steerer_who_still_holds_it(org: Org) -> None:
    """A holder of `manage_agent_sessions` takes over only a steerer who lapsed.
    While the member the steer was handed to still holds it, an admin's
    take is refused and moves nothing: the admin takes no part, and the
    member's next message still instructs."""
    sid = await org.start("steward")
    path = f"/v1/agent-sessions/{sid}"
    mia = str(org.ids["mia"])
    await org.put("mia", f"{path}/participants/{mia}")
    handed = await org.put("ann", f"{path}/principal", {"steerer": mia})
    assert handed.status_code == 200, handed.text

    taken = await org.put("ada", f"{path}/principal")
    hers = await org.client.post(
        f"{path}/messages", headers=created(org.headers["mia"]), json={"text": "Check refunds."}
    )

    assert taken.status_code == 403, taken.text
    held = (await org.client.get(path, headers=org.headers["ada"])).json()
    assert held["steerer"] == mia
    assert str(org.ids["ada"]) not in held["participants"], "a refused take joins nothing"
    assert (hers.status_code, hers.json()["participant"]) == (201, False), "hers instructs"
    moves = [s["steerer"] for s in await org.steps(sid) if s["type"] == "switched"]
    assert moves == [mia]


async def test_a_private_sessions_participants_are_the_ones_its_creator_adds(org: Org) -> None:
    """An admin sees a member's private session for audit, and an audit read
    is not a membership: the admin's self-join is refused and adds no one.
    The creator adds the admin, who takes part from then on, and the
    creator's removal through the participants route takes them out: the
    session leaves the admin's own list. A member removed from it, who
    holds no `manage_agent_sessions`, no longer sees it."""
    sid = await org.start("assistant", by="mia")
    path = f"/v1/agent-sessions/{sid}/participants"
    ada, noa = str(org.ids["ada"]), str(org.ids["noa"])

    joined = await org.put("ada", f"{path}/{ada}")
    assert joined.status_code == 403, joined.text
    assert await org.participants(sid) == [], "a refused self-join adds no one"
    assert await org.listed("ada", "mine") == []

    added = await org.put("mia", f"{path}/{ada}")
    assert (added.status_code, added.json()["participants"]) == (200, [ada])
    assert await org.listed("ada", "mine") == [sid]
    removed = await org.delete("mia", f"{path}/{ada}")
    assert (removed.status_code, removed.json()["participants"]) == (200, [])
    assert await org.listed("ada", "mine") == [], "the session leaves the admin's own list"

    await org.put("mia", f"{path}/{noa}")
    seen = await org.client.get(f"/v1/agent-sessions/{sid}", headers=org.headers["noa"])
    await org.delete("mia", f"{path}/{noa}")
    gone = await org.client.get(f"/v1/agent-sessions/{sid}", headers=org.headers["noa"])
    assert (seen.status_code, gone.status_code) == (200, 404)


async def test_a_participant_leaves_on_their_own_and_a_steerer_once_the_steer_is_handed_on(
    org: Org,
) -> None:
    """Any member joins an org-wide session. A participant leaves on their
    own, and nobody but its creator removes another, an admin included. The
    steerer is not removed while they steer: once the steer is handed back,
    they leave, and the session is never left with a steerer who takes no
    part in it."""
    sid = await org.start("steward")
    path = f"/v1/agent-sessions/{sid}"
    mia, noa, ann = str(org.ids["mia"]), str(org.ids["noa"]), str(org.ids["ann"])
    await org.put("mia", f"{path}/participants/{mia}")
    await org.put("noa", f"{path}/participants/{noa}")

    by_a_member = await org.delete("mia", f"{path}/participants/{noa}")
    by_an_admin = await org.delete("ada", f"{path}/participants/{noa}")
    left = await org.delete("noa", f"{path}/participants/{noa}")
    assert (by_a_member.status_code, by_an_admin.status_code) == (403, 403)
    assert (left.status_code, left.json()["participants"]) == (200, [mia])

    await org.put("ann", f"{path}/principal", {"steerer": mia})
    while_steering = await org.delete("mia", f"{path}/participants/{mia}")
    by_the_creator = await org.delete("ann", f"{path}/participants/{mia}")
    assert (while_steering.status_code, by_the_creator.status_code) == (409, 409)
    assert await org.participants(sid) == [mia]

    handed_back = await org.put("mia", f"{path}/principal", {"steerer": ann})
    after = await org.delete("mia", f"{path}/participants/{mia}")
    assert (handed_back.status_code, handed_back.json()["steerer"]) == (200, ann)
    assert (after.status_code, after.json()["participants"]) == (200, [])


async def test_an_admin_who_takes_a_lapsed_private_steer_takes_part_in_it(org: Org) -> None:
    """A private session's steerer leaves the org, and an admin takes the
    steer through the route that assigns a principal: the admin joins it as
    its steerer, the one self-join a private session takes besides its
    creator's adds."""
    sid = await org.start("assistant", by="mia")
    path = f"/v1/agent-sessions/{sid}"
    left = await org.client.delete(f"/v1/memberships/{org.ids['mia']}", headers=org.headers["ann"])
    assert left.status_code == 200, left.text

    taken = await org.put("ada", f"{path}/principal")

    ada = str(org.ids["ada"])
    assert (taken.status_code, taken.json()["steerer"]) == (200, ada), taken.text
    assert taken.json()["participants"] == [ada]
    assert await org.listed("ada", "mine") == [sid]
