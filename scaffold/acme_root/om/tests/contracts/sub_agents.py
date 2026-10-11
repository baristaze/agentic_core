"""Sub-agents through the engine's own tools, as a model starts them: a
tree bounded by its height and its count, children that together spend
no more than the tree's budget, a parent that parks on its children and
wakes on a report, a wait after each report, a deadline that ends a wait,
a child gated by its parent's policy as well as its own, and a spawn
asked twice that starts one child. A session with a sub-agent at work
below it is not deleted, a deleted one still holds the sub-agents below
it to its kind, and a cancel reaches a child past a deleted sibling.
Each case takes a loop over the memory storage or over Postgres, so the
unit suite and the integration suite run the same cases."""

import asyncio
from datetime import timedelta
from uuid import UUID

import pytest

from acme.om.agent_sessions.limits import deadline_park
from acme.om.agent_sessions.storage import AgentSessionStorageInterface
from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.agents.loop_rules import APPROVAL_UNLOCK
from acme.om.agents.rules import CHILDREN_PARK
from acme.om.agents.types.kind import AgentKind, DoneRule
from acme.om.agents.types.request import Spawn
from acme.om.agents.types.run import LoopRun, RunEnd
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.base import new_id
from acme.om.budgets.types.amount import Amount
from acme.om.budgets.types.budget import BudgetScope, BudgetScopeKind, WindowKind
from acme.om.context import TenantContext
from acme.om.exceptions import NotFound, ValidationFailed
from acme.om.steps.types.content import TextBlock, ToolUseBlock
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    InputHeader,
    LoopOutcome,
    Park,
    ParkReason,
    ToolFailure,
    ToolRequestHeader,
    ToolResponseHeader,
)
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.tools.native.spawn_sub_agent import SPAWN_SUB_AGENT
from acme.om.tools.native.wait_for_sub_agents import WAIT_FOR_SUB_AGENTS
from acme.om.tools.tool import TakeSnapshot
from acme.om.tools.types.policy import Decision, PolicyLayer, PolicyRule
from acme.om.tools.types.tool import ToolClass
from contracts.agent_session_storage import marked
from contracts.budget_storage import make_budget
from contracts.loops import ALLOWED, Loop, call, reply, said, use

SPAWNING = PolicyLayer(
    rules=(*ALLOWED.rules, PolicyRule(authorization_class=ToolClass.SPAWN, decision=Decision.ALLOW))
)

RESEARCH = AgentKind(
    name="research",
    version=1,
    tools=("lookup", SPAWN_SUB_AGENT, WAIT_FOR_SUB_AGENTS),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    share=Amount(cost_micros=50_000_000),
    prompts=("You test each hypothesis in a sub-agent of its own.",),
    policy=SPAWNING,
)
"""A kind that names no tree, so its tree takes the default, and starts
sub-agents of its own kind."""

SOLO = RESEARCH.model_copy(update={"name": "solo", "share": None})
"""A kind that names no share, so no spawn starts it."""

CAREFUL = RESEARCH.model_copy(
    update={
        "name": "careful",
        "tools": (*RESEARCH.tools, "note"),
        "policy": PolicyLayer(
            rules=(
                PolicyRule(authorization_class=ToolClass.READ, decision=Decision.ALLOW),
                PolicyRule(authorization_class=ToolClass.SPAWN, decision=Decision.ALLOW),
                PolicyRule(authorization_class=ToolClass.WRITE, decision=Decision.APPROVE),
            )
        ),
    }
)
"""A kind that holds every write for a person."""

LOOSE = RESEARCH.model_copy(update={"name": "loose", "tools": (*RESEARCH.tools, "note")})
"""A kind that lets a write run unattended."""

STRICT = CAREFUL.model_copy(
    update={
        "name": "strict",
        "policy": PolicyLayer(
            rules=(
                PolicyRule(authorization_class=ToolClass.READ, decision=Decision.ALLOW),
                PolicyRule(authorization_class=ToolClass.SPAWN, decision=Decision.ALLOW),
                PolicyRule(authorization_class=ToolClass.WRITE, decision=Decision.DENY),
            )
        ),
    }
)
"""A kind that never lets a write run."""

KINDS = (RESEARCH, SOLO, CAREFUL, LOOSE, STRICT)


def spawn(title: str, kind: str | None = None) -> ToolUseBlock:
    """A spawn call with an id of its own: one reply makes several."""
    objective = f"Test {title}. Read only the logs. Report what holds, with its evidence."
    asked: dict[str, object] = {"title": title, "objective": objective}
    if kind is not None:
        asked["kind"] = kind
    return ToolUseBlock(id=f"use_spawn_{new_id().hex[-12:]}", name=SPAWN_SUB_AGENT, input=asked)


def answers(steps: list[Step], tool: str) -> list[Step]:
    """The answers to every call of `tool` in the history, in the order the
    calls were made."""
    by_request = {step.responds_to: step for step in steps if step.type is StepType.TOOL_RESPONSE}
    return [
        by_request[step.id]
        for step in steps
        if isinstance(step.header, ToolRequestHeader) and step.header.tool == tool
    ]


def failure_of(answer: Step) -> ToolFailure | None:
    header = answer.header
    assert isinstance(header, ToolResponseHeader)
    return header.failure


def text_of(answer: Step) -> str:
    return "\n".join(
        part.text for part in answer.as_tool_response().parts if isinstance(part, TextBlock)
    )


async def children_of(loop: Loop, session_id: UUID) -> list[AgentSession]:
    page = await loop.managers.agent_sessions.get_children(loop.owner, session_id, None, 50)
    return list(page.items)


async def a_root(loop: Loop, ask: str) -> UUID:
    root = await loop.start(RESEARCH.name)
    await loop.say(root, ask)
    return root


async def a_root_holds_ten_sub_agents_and_the_eleventh_is_refused(loop: Loop) -> None:
    """A root of a kind that names no tree starts ten sub-agents in one turn;
    the eleventh call is the tool's failure, naming the count, and the
    model reads it and goes on."""
    root = await a_root(loop, "Test eleven hypotheses.")
    calls = [spawn(f"hypothesis {n}") for n in range(1, 12)]
    loop.anthropic.add(reply(*calls), reply(said("Ten run; the tree holds no more.")))

    run = await loop.loops.run(loop.owner, root)

    assert run.outcome is LoopOutcome.SUCCEEDED, "a bound reached is no crash"
    tree = await loop.managers.agents.tree_of(loop.owner, root)
    assert (tree.height, tree.count, tree.size) == (3, 10, 10)
    spawned = answers(await loop.history(root), SPAWN_SUB_AGENT)
    assert [failure_of(answer) for answer in spawned[:10]] == [None] * 10
    eleventh = spawned[10]
    assert failure_of(eleventh) is ToolFailure.PERMANENT
    assert "the tree holds its 10 sub-agents" in text_of(eleventh)
    children = await children_of(loop, root)
    assert len(children) == 10
    assert {child.title for child in children} == {f"hypothesis {n}" for n in range(1, 11)}


async def a_third_level_starts_and_a_fourth_is_refused(loop: Loop) -> None:
    """A sub-agent starts a child of its own, the tree's third level, of its
    own kind when the call names none; that child's own start is refused,
    naming the tree's height."""
    root = await a_root(loop, "Find where the error starts.")
    loop.anthropic.add(reply(spawn("the error")), reply(said("A sub-agent looks.")))
    await loop.loops.run(loop.owner, root)
    (child,) = await children_of(loop, root)
    loop.anthropic.add(reply(spawn("the error's first line")), reply(said("Mine looks.")))

    second = await loop.loops.run(loop.owner, child.id)

    assert second.outcome is LoopOutcome.SUCCEEDED
    (grandchild,) = await children_of(loop, child.id)
    assert (grandchild.depth, grandchild.kind) == (3, RESEARCH.name)
    (made,) = answers(await loop.history(child.id), SPAWN_SUB_AGENT)
    assert failure_of(made) is None and str(grandchild.id) in text_of(made)
    loop.anthropic.add(reply(spawn("one more level")), reply(said("I look myself.")))

    third = await loop.loops.run(loop.owner, grandchild.id)

    assert third.outcome is LoopOutcome.SUCCEEDED
    (refused,) = answers(await loop.history(grandchild.id), SPAWN_SUB_AGENT)
    assert failure_of(refused) is ToolFailure.PERMANENT
    assert "past the tree's height of 3" in text_of(refused)
    assert await children_of(loop, grandchild.id) == []
    assert (await loop.managers.agents.tree_of(loop.owner, root)).size == 2


async def children_together_spend_no_more_than_the_trees_budget(loop: Loop) -> None:
    """The root's budget bounds the tree: three children, each with a share
    far above it, spend from what the tree has left, one until its loop
    ends and two until the tree's budget refuses them, and what the tree
    spent and holds never passes it. A spawn of a kind that names no share
    is refused, and starts nothing."""
    root = await a_root(loop, "Test three hypotheses.")
    cap = 340_000  # one call's worst case, about 320,000, and some calls besides
    budget = await loop.managers.budgets.create_budget(
        loop.owner,
        make_budget(BudgetScopeKind.TREE, str(root), window=WindowKind.LIFE, cost_micros=cap),
    )
    calls = [spawn(f"hypothesis {n}") for n in (1, 2, 3)]
    loop.anthropic.add(
        reply(*calls, spawn("a hypothesis alone", kind=SOLO.name)),
        reply(said("Three sub-agents test them.")),
    )

    await loop.loops.run(loop.owner, root)

    spawned = answers(await loop.history(root), SPAWN_SUB_AGENT)
    assert [failure_of(answer) for answer in spawned] == [None, None, None, ToolFailure.PERMANENT]
    assert "names no share" in text_of(spawned[3])
    children = sorted(await children_of(loop, root), key=lambda child: child.title)
    assert [child.kind for child in children] == [RESEARCH.name] * 3, "the unshared one never made"
    first, second, third = children
    loop.anthropic.add(
        *[reply(use("lookup", f"page {n}")) for n in (1, 2)], reply(said("It holds."))
    )
    loop.anthropic.add(*[reply(use("lookup", f"page {n}")) for n in range(3, 43)])

    ended = await loop.loops.run(loop.owner, first.id)
    refused = [await loop.loops.run(loop.owner, child.id) for child in (second, third)]

    assert ended.outcome is LoopOutcome.SUCCEEDED
    for run in refused:
        assert run.park is not None and run.park.reason is ParkReason.BUDGET
        assert run.park.unlock == str(budget.id), "the tree's budget refused it, not its share"
    tally = await loop.managers.budgets.get_spend(loop.owner, budget.id)
    assert tally.spent_cost_micros + tally.held_cost_micros <= cap
    spent = [await child_spend(loop, child.id) for child in children]
    assert all(child > 0 for child in spent), "each child spent from the tree"
    assert sum(spent) < tally.spent_cost_micros, "and the root spent from it too"


async def child_spend(loop: Loop, session_id: UUID) -> int:
    """What a child spent, as its share's budget on its own session counts
    it."""
    page = await loop.managers.budgets.get_budgets(loop.owner, None, 50)
    scope = BudgetScope(kind=BudgetScopeKind.SESSION, key=str(session_id))
    (share,) = [budget for budget in page.items if budget.scope == scope]
    tally = await loop.managers.budgets.get_spend(loop.owner, share.id)
    return tally.spent_cost_micros + tally.held_cost_micros


async def a_parent_parks_on_its_children_and_a_report_wakes_it(loop: Loop) -> None:
    """The wait answers the running child and parks the parent on
    `children`, with no model call until the child's report clears it. The
    woken parent reads the report; with no child running, the wait is
    refused, and the loop goes on without parking."""
    root = await a_root(loop, "What is the quarterly total?")
    loop.anthropic.add(reply(spawn("the total")), reply(call(WAIT_FOR_SUB_AGENTS)))

    parked = await loop.loops.run(loop.owner, root)

    assert parked.end is RunEnd.PARKED and parked.park == CHILDREN_PARK
    session = await loop.managers.agent_sessions.get_session(loop.owner, root)
    assert (session.status, session.park) == (SessionStatus.PARKED, CHILDREN_PARK)
    (child,) = await children_of(loop, root)
    (waited,) = answers(await loop.history(root), WAIT_FOR_SUB_AGENTS)
    assert failure_of(waited) is None and str(child.id) in text_of(waited)
    calls = len(loop.anthropic.calls)
    loop.anthropic.add(reply(said("The total is 12.")))

    done = await loop.loops.run(loop.owner, child.id)

    assert done.outcome is LoopOutcome.SUCCEEDED
    assert len(loop.anthropic.calls) == calls + 1, "the child's call alone"
    woken = await loop.managers.agent_sessions.get_session(loop.owner, root)
    assert (woken.status, woken.park) == (SessionStatus.PENDING, None)
    loop.anthropic.add(reply(call(WAIT_FOR_SUB_AGENTS)), reply(said("The total is 12.")))

    resumed = await loop.loops.run(loop.owner, root)

    assert resumed.outcome is LoopOutcome.SUCCEEDED, "a refused wait parks nothing"
    read = "\n".join(
        block.text
        for message in loop.anthropic.calls[calls + 1].messages
        for block in message.blocks
        if isinstance(block, TextBlock)
    )
    assert f"Sub-agent {child.id}" in read and "The total is 12." in read
    second = answers(await loop.history(root), WAIT_FOR_SUB_AGENTS)[1]
    assert failure_of(second) is ToolFailure.PERMANENT
    assert "no sub-agent of yours is running" in text_of(second)


async def a_root_waits_after_each_of_seven_reports_and_reads_them_all(loop: Loop) -> None:
    """A root starts seven sub-agents and waits after each report, the same
    call each time, seven times past an error streak of five. A wait a
    report woke is no repeat of the one before it: the root reads all seven
    reports and ends on its own answer, never `inconclusive`."""
    root = await a_root(loop, "Test seven hypotheses.")
    calls = [spawn(f"hypothesis {n}") for n in range(1, 8)]
    loop.anthropic.add(reply(*calls), reply(call(WAIT_FOR_SUB_AGENTS)))

    first = await loop.loops.run(loop.owner, root)

    assert first.park == CHILDREN_PARK
    children = await children_of(loop, root)
    assert len(children) == 7
    runs: list[LoopRun] = []
    for n, child in enumerate(children, start=1):
        loop.anthropic.add(reply(said(f"{child.title} holds.")))
        await loop.loops.run(loop.owner, child.id)
        last = n == len(children)
        loop.anthropic.add(reply(said("All seven hold.") if last else call(WAIT_FOR_SUB_AGENTS)))
        runs.append(await loop.loops.run(loop.owner, root))
        if not last:
            assert runs[-1].park == CHILDREN_PARK, f"the wait after report {n} parks"

    assert [run.end for run in runs] == [RunEnd.PARKED] * 6 + [RunEnd.ENDED]
    assert runs[-1].outcome is LoopOutcome.SUCCEEDED
    waits = answers(await loop.history(root), WAIT_FOR_SUB_AGENTS)
    assert [failure_of(answer) for answer in waits] == [None] * 7
    read = "\n".join(
        block.text
        for message in loop.anthropic.calls[-1].messages
        for block in message.blocks
        if isinstance(block, TextBlock)
    )
    assert all(f"Sub-agent {child.id}" in read for child in children), "every report read"


async def the_deadline_ends_a_wait_on_children(loop: Loop) -> None:
    """The tree's deadline passes while a child works. The child parks on it,
    and that ends its parent's wait, since no report can come before a
    person moves the deadline: the parent leaves its park on `children` and
    parks on the deadline, where its person is asked, as a single agent
    would, with no model call. Moved, the deadline lets the parent wait on
    its child again."""
    root = await a_root(loop, "What is the quarterly total?")
    deadline = loop.clock.now + timedelta(hours=1)
    await loop.managers.agents.set_deadline(loop.owner, root, deadline)
    loop.anthropic.add(reply(spawn("the total")), reply(call(WAIT_FOR_SUB_AGENTS)))
    waiting = await loop.loops.run(loop.owner, root)
    assert waiting.park == CHILDREN_PARK
    (child,) = await children_of(loop, root)
    calls = len(loop.anthropic.calls)
    loop.clock.now = deadline + timedelta(seconds=1)

    stopped = await loop.loops.run(loop.owner, child.id)

    assert stopped.park == deadline_park()
    sessions = loop.managers.agent_sessions
    woken = await sessions.get_session(loop.owner, root)
    assert (woken.status, woken.park) == (SessionStatus.PENDING, None), "its wait ended"

    parked = await loop.loops.run(loop.owner, root)

    assert parked.park == deadline_park(), "where its person is asked"
    assert len(loop.anthropic.calls) == calls, "no model call past the deadline"
    await loop.managers.agents.set_deadline(loop.owner, root, loop.clock.now + timedelta(hours=1))

    again = await loop.loops.run(loop.owner, root)

    assert again.park == CHILDREN_PARK, "the moved deadline lets it wait again"
    assert len(loop.anthropic.calls) == calls


async def a_looser_kind_runs_no_call_its_parents_kind_would_hold(loop: Loop) -> None:
    """A root whose kind holds every write for a person starts a sub-agent of
    a kind that lets a write run unattended. The child's write is decided
    under its parent's kind as well as its own, and the stricter holds: it
    waits for a person, and never runs."""
    root = await loop.start(CAREFUL.name)
    await loop.say(root, "Note the total.")
    loop.anthropic.add(
        reply(spawn("the note", kind=LOOSE.name)), reply(said("A sub-agent notes it."))
    )
    await loop.loops.run(loop.owner, root)
    (child,) = await children_of(loop, root)
    assert child.kind == LOOSE.name
    loop.anthropic.add(reply(use("note", "the total")))

    held = await loop.loops.run(loop.owner, child.id)

    assert held.park == Park(reason=ParkReason.PERSON, unlock=APPROVAL_UNLOCK)
    assert loop.tools["note"].ran_as == [], "the write never ran"


async def a_report_that_lands_before_the_park_still_wakes_the_parent(
    loop: Loop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A report can land after the run read its history and before its park
    is written, when there is no park yet for it to clear. The run reads
    what came since and clears its own park, so the parent never waits on a
    report it already holds."""
    root = await a_root(loop, "What is the quarterly total?")
    loop.anthropic.add(reply(spawn("the total")), reply(call(WAIT_FOR_SUB_AGENTS)))
    sessions = loop.managers.agent_sessions
    park = sessions.park
    reported: list[Step] = []

    async def report_first(
        ctx: TenantContext, session_id: UUID, epoch: int, loop_id: UUID, held: Park
    ) -> AgentSession:
        if session_id == root and not reported:
            (child,) = await children_of(loop, root)
            loop.anthropic.add(reply(said("The total is 12.")))
            await loop.loops.run(loop.owner, child.id)
            reported.extend(
                step
                for step in await loop.history(root)
                if isinstance(step.header, InputHeader) and step.header.agent is not None
            )
        return await park(ctx, session_id, epoch, loop_id, held)

    monkeypatch.setattr(sessions, "park", report_first)

    parked = await loop.loops.run(loop.owner, root)

    assert parked.park == CHILDREN_PARK and len(reported) == 1, "the report came first"
    woken = await sessions.get_session(loop.owner, root)
    assert (woken.status, woken.park) == (SessionStatus.PENDING, None)


async def a_spawn_asked_twice_starts_one_child(loop: Loop, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run lost while its spawn ran leaves the call open; the run that
    takes the loop up asks it again, under the same id, and gets the child
    the first made. The tree holds one child, and the lost run's answer is
    refused."""
    root = await a_root(loop, "Test one hypothesis.")
    loop.anthropic.add(reply(spawn("the hypothesis")), reply(said("A sub-agent tests it.")))
    agents = loop.managers.agents
    real = agents.spawn
    asked: list[UUID] = []
    started, release = asyncio.Event(), asyncio.Event()

    async def held(
        ctx: TenantContext, parent_id: UUID, request: Spawn, fork: TakeSnapshot | None = None
    ) -> AgentSession:
        asked.append(request.id)
        child = await real(ctx, parent_id, request, fork)
        if len(asked) == 1:
            started.set()
            await release.wait()
        return child

    monkeypatch.setattr(agents, "spawn", held)
    lost = asyncio.ensure_future(loop.loops.run(loop.owner, root))
    await started.wait()

    run = await loop.loops.run(loop.owner, root)
    release.set()
    stale = await lost

    assert run.outcome is LoopOutcome.SUCCEEDED and stale.end is RunEnd.STALE
    assert len(asked) == 2 and len(set(asked)) == 1, "asked twice, under one id"
    (child,) = await children_of(loop, root)
    assert child.id == asked[0], "the child's id is the call's own"
    assert (await agents.tree_of(loop.owner, root)).size == 1
    (answer,) = answers(await loop.history(root), SPAWN_SUB_AGENT)
    assert failure_of(answer) is None and str(child.id) in text_of(answer)


async def a_strict_tree(loop: Loop) -> tuple[AgentSession, AgentSession]:
    """A root whose kind never lets a write run starts a sub-agent of a kind
    that does, and that sub-agent starts one of its own and ends its loop.
    Answers the middle session, idle, and the one below it, waiting to
    run."""
    root = await loop.start(STRICT.name)
    await loop.say(root, "Note the total.")
    loop.anthropic.add(
        reply(spawn("the note", kind=LOOSE.name)), reply(said("A sub-agent notes it."))
    )
    await loop.loops.run(loop.owner, root)
    (child,) = await children_of(loop, root)
    loop.anthropic.add(reply(spawn("the note's line")), reply(said("Mine notes it.")))
    await loop.loops.run(loop.owner, child.id)
    (grandchild,) = await children_of(loop, child.id)
    child = await loop.managers.agent_sessions.get_session(loop.owner, child.id)
    assert (child.status, grandchild.depth) == (SessionStatus.IDLE, 3)
    return child, grandchild


async def mark_deleted(loop: Loop, session_id: UUID, *, claimed: bool = False) -> None:
    """The session marked deleted under the storage, and claimed for its
    purge when `claimed`, whatever the manager would refuse."""
    storage = loop.storage.get_agent_session_storage()
    found = await storage.read_session(loop.owner.org_id, session_id)
    assert found is not None
    gone = marked(found, found.version + 1, loop.clock(), claimed=claimed)
    await storage.write_session(loop.owner.org_id, gone, found.version, ())


async def a_session_with_a_sub_agent_at_work_below_it_is_not_deleted(loop: Loop) -> None:
    """A middle session whose own loop ended is not deleted while the
    sub-agent it started runs, and the refusal names that sub-agent. Once
    nothing below it is at work, it is deleted."""
    child, grandchild = await a_strict_tree(loop)
    sessions = loop.managers.agent_sessions
    lookup = loop.tools["lookup"]
    lookup.holds = True
    loop.anthropic.add(reply(use("lookup")), reply(said("The total is 12.")))
    running = asyncio.ensure_future(loop.loops.run(loop.owner, grandchild.id))
    await lookup.started.wait()

    with pytest.raises(ValidationFailed) as refused:
        await sessions.delete_session(loop.owner, child.id)

    assert f"has sub-agent {grandchild.id}" in refused.value.message
    assert (await sessions.get_session(loop.owner, child.id)).deleted_at is None
    lookup.release.set()
    assert (await running).outcome is LoopOutcome.SUCCEEDED
    # The report of its sub-agent's end woke it: its own loop ends first.
    loop.anthropic.add(reply(said("The note is made.")))
    await loop.loops.run(loop.owner, child.id)
    assert (await sessions.delete_session(loop.owner, child.id)).deleted_at is not None


async def a_deleted_session_still_holds_the_sub_agents_below_it_to_its_kind(
    loop: Loop,
) -> None:
    """A middle session marked deleted takes no kind out of what its
    sub-agent's calls are decided under: a write the root's kind never lets
    run is still denied, never held for a person, and never runs."""
    child, grandchild = await a_strict_tree(loop)
    await mark_deleted(loop, child.id)
    with pytest.raises(NotFound):
        await loop.managers.agent_sessions.get_session(loop.owner, child.id)
    loop.anthropic.add(reply(use("note", "the total")), reply(said("I cannot note it.")))

    run = await loop.loops.run(loop.owner, grandchild.id)

    assert run.outcome is LoopOutcome.SUCCEEDED and run.park is None
    (denied,) = answers(await loop.history(grandchild.id), "note")
    assert failure_of(denied) is ToolFailure.DENIED
    assert loop.tools["note"].ran_as == [], "the write never ran"


async def a_sub_agent_whose_ancestor_is_purged_runs_no_loop(
    loop: Loop, purging: AgentSessionStorageInterface
) -> None:
    """A middle session past its purge leaves no kind to read: the loop of
    the sub-agent below it ends errored before any model call, so no call
    is decided under less than every kind above it. `purging` holds the
    purge login, which the loop's own storage does not."""
    child, grandchild = await a_strict_tree(loop)
    await mark_deleted(loop, child.id, claimed=True)
    assert await purging.purge_session(loop.owner.org_id, child.id)
    calls = len(loop.anthropic.calls)

    run = await loop.loops.run(loop.owner, grandchild.id)

    assert run.outcome is LoopOutcome.ERRORED
    assert len(loop.anthropic.calls) == calls, "no model call"


async def a_cancel_reaches_a_child_past_a_deleted_sibling(loop: Loop) -> None:
    """A root starts two children. The first ends, and a person deletes it;
    the second has not run. A person cancels the root, and the cascade
    passes over the deleted child: the second ends cancelled, with no model
    call after the cancel."""
    root = await a_root(loop, "What are the quarterly total and count?")
    loop.anthropic.add(
        reply(spawn("the total"), spawn("the count")), reply(call(WAIT_FOR_SUB_AGENTS))
    )
    assert (await loop.loops.run(loop.owner, root)).park == CHILDREN_PARK
    first, second = await children_of(loop, root)
    loop.anthropic.add(reply(said("The total is 12.")))
    assert (await loop.loops.run(loop.owner, first.id)).outcome is LoopOutcome.SUCCEEDED
    sessions = loop.managers.agent_sessions
    await sessions.delete_session(loop.owner, first.id)
    assert (await sessions.get_session(loop.owner, second.id)).status is SessionStatus.PENDING
    calls = len(loop.anthropic.calls)
    cancel = Step(
        id=new_id(),
        created_at=loop.clock(),
        session_id=root,
        loop_id=new_id(),
        type=StepType.CONTROL,
        actor=Actor.PERSON,
        origin=Origin.PORTAL,
        header=ControlHeader(command=ControlCommand.CANCEL),
    )
    await loop.managers.steps.append_inputs(loop.owner, root, [cancel])

    cancelled = await loop.loops.run(loop.owner, root)

    assert cancelled.outcome is LoopOutcome.CANCELLED
    (last,) = (await loop.history(second.id))[-1:]
    assert isinstance(last.header, ControlHeader), "the cascade reached the second child"
    assert last.header.command is ControlCommand.CANCEL
    stopped = await loop.loops.run(loop.owner, second.id)
    assert stopped.outcome is LoopOutcome.CANCELLED
    assert len(loop.anthropic.calls) == calls, "no model call after the cancel"
