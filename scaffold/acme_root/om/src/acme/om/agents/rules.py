"""Pure rules of agent kinds and trees: what a turn means under a kind's
done rule, whether a tree has room for one more child, the tree a root
starts with, and the claim no gate is asked about. Values in, values out;
no clock, no storage."""

from datetime import datetime
from uuid import UUID

from acme.om.agents.types.kind import AgentKind, DoneRule
from acme.om.agents.types.result import Claim, Result, Turn
from acme.om.agents.types.tree import AgentTree
from acme.om.steps.types.header import LoopOutcome
from acme.om.steps.types.step import Step

OUTCOMES: dict[Claim, LoopOutcome] = {
    Claim.SUCCEEDED: LoopOutcome.SUCCEEDED,
    Claim.FAILED: LoopOutcome.FAILED,
}
"""The outcome an accepted result ends its loop with."""


def after_turn(kind: AgentKind, response: Step, nudges: int) -> Turn:
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
