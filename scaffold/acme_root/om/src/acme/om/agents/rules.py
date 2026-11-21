"""Pure rules of agent kinds and trees: what a turn means under a kind's
done rule, who may start a kind, whether a tree has room for one more
child, the tree a root starts with, the claim no gate is asked about,
what a child's report tells its parent, the park a parent waits for it
on and what ends it, and what a kind whose loop runs in a request may
hold and why its loop ended. Values in, values out; no clock, no
storage."""

from collections.abc import Mapping
from datetime import datetime, timedelta
from uuid import UUID

from acme.infra.agent_workspaces import IsolationMode
from acme.om.agent_sessions.limits import DEADLINE_UNLOCK
from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.agent_steps.types.agent_step import AgentStep
from acme.om.agent_steps.types.header import LoopOutcome, Park, ParkReason
from acme.om.agents.types.kind import (
    REQUEST_STEPS,
    REQUEST_TIME,
    AgentKind,
    AgentKindCatalog,
    DoneRule,
    RunsIn,
    Starter,
)
from acme.om.agents.types.report import Report
from acme.om.agents.types.result import Claim, Result, Turn
from acme.om.agents.types.tree import AgentTree
from acme.om.exceptions import UnknownAgentKind
from acme.om.tools.rules import decide, with_reach
from acme.om.tools.types.policy import Decision, ToolPolicyCall, ToolPolicyLayer
from acme.om.tools.types.tool import ToolClass, ToolMode, ToolSpec

OUTCOMES: dict[Claim, LoopOutcome] = {
    Claim.SUCCEEDED: LoopOutcome.SUCCEEDED,
    Claim.FAILED: LoopOutcome.FAILED,
}
"""The outcome an accepted result ends its loop with."""

CHILDREN_PARK = Park(reason=ParkReason.CHILDREN, unlock="report")
"""The park of a loop whose agent waits on its sub-agents: a child's report
that wakes the parent clears it."""


def after_turn(kind: AgentKind, response: AgentStep, nudges: int) -> Turn:
    """What a model response means under its kind's done rule. `nudges` is
    how many turns in a row before this one neither continued nor
    submitted. For an assistant, a turn with no tool call is the answer.
    For a delivery agent, only its result tool ends a loop: a turn with no
    call earns a nudge, and one past the kind's nudges ends the loop
    inconclusive."""
    uses = response.as_tool_uses()
    if kind.done_rule is DoneRule.ANSWER:
        return Turn.CALLS if uses else Turn.ANSWERED
    if any(use.name == kind.result_tool for use in uses):
        return Turn.SUBMITTED
    if uses:
        return Turn.CALLS
    return Turn.EXHAUSTED if nudges + 1 >= kind.max_nudges else Turn.NUDGE


STARTS: dict[Starter, str] = {
    Starter.PERSON: "a person starts it, or confirms a hand-off to it",
    Starter.AGENT: "an agent starts it as its sub-agent",
}
"""What each starter of a kind is, as a refusal names the one it lacks."""


def start_refusal(kind: AgentKind, starter: Starter) -> str | None:
    """Why `starter` may not start a session of `kind`, or None when it may:
    a person starts, or confirms a hand-off to, only a kind whose
    `started_by` names `person`, and a spawn starts only one that names
    `agent`."""
    if starter in kind.started_by:
        return None
    return f"agent kind {kind.name} is started only when " + " or ".join(
        STARTS[allowed] for allowed in sorted(kind.started_by)
    )


def tree_refusal(tree: AgentTree, depth: int) -> str | None:
    """Why a tree has no room for a child at `depth`, or None when it has:
    the child would be deeper than the tree's height, or the tree holds its
    count of sub-agents already."""
    if depth > tree.height:
        return f"a child at depth {depth} is past the tree's height of {tree.height}"
    if tree.size >= tree.count:
        return f"the tree holds its {tree.count} sub-agents"
    return None


def tree_for(
    kind: AgentKind, root_id: UUID, deadline: datetime | None, now: datetime, by: UUID
) -> AgentTree:
    """The tree a root session of `kind` starts: the kind's bounds, and one
    instant for its deadline, the one given or the kind's span from now."""
    if deadline is None and kind.deadline is not None:
        deadline = now + kind.deadline
    return AgentTree(
        id=root_id,
        created_at=now,
        updated_at=now,
        created_by=by,
        updated_by=by,
        height=kind.tree.height,
        count=kind.tree.count,
        concurrency=kind.tree.concurrency,
        deadline=deadline,
    )


def claim_refusal(result: Result) -> str | None:
    """Why a result is refused before any gate looks at it: a claim, of
    success or of failure, that cites no evidence at all."""
    if not result.evidence:
        return f"a claim that the work {result.claim.value} cites its evidence"
    return None


def notes_parent(park: Park) -> bool:
    """Whether a child's park reaches its parent: one on a person, the time
    a child needs one. The tree's deadline is the parent's too, and a wait
    on a budget or a provider belongs to the tree, unlocked at its root, so
    none of them disturbs the parent."""
    return park.reason is ParkReason.PERSON and park.unlock != DEADLINE_UNLOCK


def ends_parents_wait(park: Park) -> bool:
    """Whether a child's park ends its parent's wait on its children though
    it tells the parent nothing: one on the tree's deadline. The deadline
    is the parent's too, and no report can come before a person moves it,
    so the parent leaves the wait and parks on the deadline itself."""
    return park.reason is ParkReason.PERSON and park.unlock == DEADLINE_UNLOCK


def report_wakes(report: Report) -> bool:
    """Whether a child's report wakes its parent: every one does but the
    note of a cancel that came down from the parent. The parent stopped it
    already, and a parent whose own loop a principal cancelled never starts
    again on its children's word."""
    return not report.cancelled_by_parent


def report_text(child: AgentSession, report: Report) -> str:
    """What a parent reads of its child's report: which child, how its loop
    stands, and the last thing it said. The child's title is opened first,
    and says nothing once the child's key is revoked."""
    who = f'Sub-agent {child.id} ("{child.title.said()}", {child.kind} v{child.kind_version})'
    if report.outcome is not None:
        text = f"{who} ended {report.outcome.value}."
    else:
        assert report.park is not None
        text = f"{who} waits for a person ({report.park.unlock})."
    if report.accepted is not None:
        checked = "verified" if report.accepted.verified else "unverified"
        text += f" Its result was accepted, {checked}."
    if not report.answer:
        return f"{text} It said nothing."
    return f"{text} Its last answer:\n\n{report.answer}"


def request_kind_breaches(
    kind: AgentKind, specs: Mapping[str, ToolSpec], ceilings: ToolPolicyLayer
) -> tuple[str, ...]:
    """What a kind whose loop runs in a request breaks of what such a kind
    may hold, each named; none for a kind that runs in a worker. A request
    holds no workspace, starts no sub-agent and no job, is started by no
    agent as its sub-agent, and runs no call that waits for a person: each tool it names is one of `specs`, the
    catalog's, and its own layer of policy allows it under the platform's
    `ceilings`. It makes at most `REQUEST_STEPS` model calls, within
    `REQUEST_TIME`. A tenant's layer may still hold a call for a person at
    run time, and the loop then ends where it would park."""
    if kind.runs_in is not RunsIn.REQUEST:
        return ()
    breaches: list[str] = []
    if kind.isolation.mode is not IsolationMode.NONE:
        breaches.append(f"it works in a {kind.isolation.mode.value} workspace")
    if Starter.AGENT in kind.started_by:
        breaches.append("an agent may start it as its sub-agent")
    for name in kind.tools:
        spec = specs.get(name)
        if spec is None:
            breaches.append(f"{name} is no tool of the catalog")
            continue
        if spec.authorization_class == ToolClass.SPAWN:
            breaches.append(f"{name} starts a session, a sub-agent's or a handoff's")
        if spec.mode is ToolMode.JOB:
            breaches.append(f"{name} starts a job, which outlives the request")
        asked = ToolPolicyCall(
            tool=name, authorization_class=spec.authorization_class, effect=spec.effect
        )
        if decide(with_reach(asked), kind.policy, ToolPolicyLayer(), ceilings) is Decision.APPROVE:
            breaches.append(f"{name} waits for a person's approval")
    if kind.limits.step_guard > REQUEST_STEPS:
        breaches.append(
            f"its step guard of {kind.limits.step_guard} is past {REQUEST_STEPS} model calls"
        )
    if kind.limits.run_time > REQUEST_TIME:
        breaches.append(
            f"its run time of {_seconds(kind.limits.run_time)} is past " + _seconds(REQUEST_TIME)
        )
    return tuple(breaches)


def request_kind_refusal(
    kinds: tuple[AgentKind, ...], specs: Mapping[str, ToolSpec], ceilings: ToolPolicyLayer
) -> str | None:
    """Why publishing `kinds` is refused, every breach of every request kind
    among them named, or None when none breaks anything."""
    refused = [
        f"agent kind {kind.name} v{kind.version} runs in a request, and " + "; ".join(breaches)
        for kind in kinds
        if (breaches := request_kind_breaches(kind, specs, ceilings))
    ]
    return " | ".join(refused) or None


def runs_in_request(kinds: AgentKindCatalog, session: AgentSession) -> bool:
    """Whether the session's loop runs in the request that asks it, as its
    kind says at every version. A kind the catalog no longer holds ran in a
    worker."""
    try:
        return kinds.latest(session.kind).runs_in is RunsIn.REQUEST
    except UnknownAgentKind:
        return False


def request_end_cause(park: Park | None, run_time: timedelta) -> str:
    """Why a loop that runs in a request ended where another loop would park
    or yield: nothing waits in a request. Named from the park's reason, what
    clears it, and its cause, or from the run time a yield passed: names and
    ids, never content."""
    if park is None:
        return f"a request waits for nothing: the run passed its run time of {_seconds(run_time)}"
    said = f"a request waits for nothing: the loop parks on {park.reason.value} ({park.unlock})"
    return said if park.cause is None else f"{said}: {park.cause}"


def _seconds(span: timedelta) -> str:
    return f"{span.total_seconds():g} seconds"
