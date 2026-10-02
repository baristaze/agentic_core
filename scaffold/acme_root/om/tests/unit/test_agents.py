"""Agent kinds and the sessions they form: the catalog and the done rules,
the result gate and its null, sub-agents bounded by their parent and their
tree, the cancel that cascades, and the work one kind hands another, over
the memory storage."""

from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.agent_session_storage import make_session
from contracts.doubles import context, model_request
from contracts.factories import make_org
from contracts.step_storage import make_message, make_parked, make_request, make_response
from pydantic import ValidationError

from acme.infra.impl.local import InfraLocalImpl
from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.agents import ResultGateInterface
from acme.om.agents.rules import after_turn, claim_refusal, tree_refusal
from acme.om.agents.types.kind import AgentKind, AgentKindCatalog, DoneRule, TreeLimits
from acme.om.agents.types.request import Handoff, Spawn, Start
from acme.om.agents.types.result import Claim, Result, Turn, Verdict
from acme.om.agents.types.tree import AgentTree
from acme.om.attribution.rules import trust_of
from acme.om.attribution.types.authority import AuthorityMode, Trust
from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.base import new_id, utcnow
from acme.om.context import Role, TenantContext
from acme.om.exceptions import (
    NotAuthorized,
    TreeBoundReached,
    UnknownAgentKind,
    ValidationFailed,
)
from acme.om.root import Managers, build_managers
from acme.om.steps.types.content import Content, TextBlock, ToolUseBlock
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    InputHeader,
    LoopEndedHeader,
    LoopOutcome,
    ModelResponseHeader,
)
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.storage.impl.memory import StorageMemoryImpl

DELIVERY = AgentKind(
    name="delivery",
    version=2,
    tools=("read_log", "run_tests", "push_branch", "spawn", "submit"),
    done_rule=DoneRule.RESULT_TOOL,
    result_tool="submit",
    max_nudges=2,
    authority=AuthorityMode.STEADY,
    tree=TreeLimits(height=3, count=3),
    deadline=timedelta(hours=8),
)
OLDER = DELIVERY.model_copy(update={"version": 1})
HELPER = AgentKind(
    name="helper",
    version=1,
    tools=("read_log", "fetch_url", "run_tests", "submit"),
    done_rule=DoneRule.RESULT_TOOL,
    result_tool="submit",
    authority=AuthorityMode.STEADY,
    tree=TreeLimits(height=1, count=0),
)
ASSISTANT = AgentKind(
    name="assistant",
    version=1,
    tools=("read_records", "spawn"),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    tree=TreeLimits(height=2, count=1),
)
REPORTER = HELPER.model_copy(
    update={"name": "reporter", "tools": ("read_log", "report"), "result_tool": "report"}
)
KINDS = (OLDER, DELIVERY, HELPER, ASSISTANT, REPORTER)


def person_of(ctx: TenantContext) -> Principal:
    return Principal(kind=PrincipalKind.PERSON, id=ctx.user_id)


def turn(*uses: str) -> Step:
    """A model response that calls the named tools, or answers with none."""
    blocks = [ToolUseBlock(id=f"call_{n}", name=name) for n, name in enumerate(uses)]
    return Step(
        id=new_id(),
        created_at=utcnow(),
        session_id=new_id(),
        loop_id=new_id(),
        type=StepType.MODEL_RESPONSE,
        actor=Actor.MODEL,
        origin=Origin.ENGINE,
        responds_to=new_id(),
        header=ModelResponseHeader(),
        content=Content(blocks=(TextBlock(text="the total is off by one"), *blocks)),
    )


# The kinds and their rules.


def test_a_catalog_holds_every_version_and_answers_the_latest() -> None:
    catalog = AgentKindCatalog(kinds=KINDS)
    assert catalog.latest("delivery") == DELIVERY
    assert catalog.get("delivery", 1) == OLDER
    with pytest.raises(UnknownAgentKind):
        catalog.get("delivery", 3)
    with pytest.raises(UnknownAgentKind):
        catalog.latest("reviewer")
    with pytest.raises(ValidationError):
        AgentKindCatalog(kinds=(DELIVERY, DELIVERY))


@pytest.mark.parametrize(
    "broken",
    [
        {"tools": ("read_log", "read_log", "submit")},
        {"result_tool": None},
        {"result_tool": "deploy"},
        {"done_rule": DoneRule.ANSWER},
    ],
)
def test_a_kind_that_contradicts_itself_is_refused(broken: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AgentKind.model_validate({**DELIVERY.model_dump(), **broken})


def test_an_assistant_is_done_when_a_turn_calls_no_tool() -> None:
    assert after_turn(ASSISTANT, turn(), 0) is Turn.ANSWERED
    assert after_turn(ASSISTANT, turn("read_records"), 0) is Turn.CALLS


def test_only_the_result_tool_ends_a_delivery_loop_and_nudges_run_out() -> None:
    assert after_turn(DELIVERY, turn("read_log", "submit"), 0) is Turn.SUBMITTED
    assert after_turn(DELIVERY, turn("read_log"), 1) is Turn.CALLS
    assert after_turn(DELIVERY, turn(), 0) is Turn.NUDGE
    assert after_turn(DELIVERY, turn(), 1) is Turn.EXHAUSTED


def test_a_claim_with_no_evidence_is_refused_before_any_gate() -> None:
    cited = (new_id(),)
    assert claim_refusal(Result(claim=Claim.SUCCEEDED)) is not None
    assert claim_refusal(Result(claim=Claim.FAILED)) is not None
    assert claim_refusal(Result(claim=Claim.SUCCEEDED, evidence=cited)) is None
    with pytest.raises(ValidationError):
        Verdict(accepted=True, verified=True)
    with pytest.raises(ValidationError):
        Verdict(accepted=False, verified=True, reason="no")


def test_a_tree_has_room_within_its_height_and_its_count() -> None:
    now, by = utcnow(), new_id()
    tree = AgentTree(
        id=new_id(), created_at=now, updated_at=now, created_by=by, updated_by=by, height=2, count=2
    )
    assert tree_refusal(tree, 2) is None
    assert tree_refusal(tree, 3) is not None
    assert tree_refusal(tree.model_copy(update={"size": 2}), 2) is not None


# The manager.


class Refusing(ResultGateInterface):
    """A gate that knows what evidence is, and finds none it accepts."""

    async def check(self, ctx: TenantContext, session_id: UUID, result: Result) -> Verdict:
        return Verdict(accepted=False, reason="the cited run did not pass")


@pytest.fixture
def managers(tmp_path: Path) -> Managers:
    return build_managers(StorageMemoryImpl(), InfraLocalImpl(tmp_path), agent_kinds=KINDS)


async def start(managers: Managers, ctx: TenantContext, kind: str = "delivery") -> AgentSession:
    return await managers.agents.start_session(
        ctx, Start(id=new_id(), kind=kind, title="the weekly report is missing a total")
    )


def ended(session_id: UUID, loop_id: UUID) -> Step:
    return Step(
        id=new_id(),
        created_at=utcnow(),
        session_id=session_id,
        loop_id=loop_id,
        type=StepType.LOOP_ENDED,
        actor=Actor.ENGINE,
        origin=Origin.ENGINE,
        header=LoopEndedHeader(outcome=LoopOutcome.SUCCEEDED),
    )


def spawn(kind: str = "helper") -> Spawn:
    return Spawn(id=new_id(), kind=kind, title="reproduce it", objective="run the failing test")


async def test_a_session_starts_on_the_latest_version_with_its_tree(
    managers: Managers,
) -> None:
    ctx = context(Role.MEMBER)
    before = utcnow()
    session = await start(managers, ctx)
    assert (session.kind, session.kind_version) == ("delivery", 2)
    authority = await managers.attribution.get_authority(ctx, session.id)
    assert (authority.mode, authority.principal) == (AuthorityMode.STEADY, person_of(ctx))
    assert session.tools == DELIVERY.tools and session.depth == 1
    tree = await managers.agents.tree_of(ctx, session.id)
    assert (tree.id, tree.height, tree.count, tree.size) == (session.id, 3, 3, 0)
    span = DELIVERY.deadline
    assert span is not None and tree.deadline is not None and before + span <= tree.deadline
    again = Start(id=session.id, kind="delivery", title="again")
    assert await managers.agents.start_session(ctx, again) == session
    with pytest.raises(UnknownAgentKind):
        await start(managers, ctx, "reviewer")


async def test_a_child_holds_no_more_than_its_parent(managers: Managers) -> None:
    org = make_org()
    owner, teammate = context(Role.MEMBER, org), context(Role.MEMBER, org)
    parent = await start(managers, owner)
    child = await managers.agents.spawn(owner, parent.id, spawn())
    assert child.tools == ("read_log", "run_tests", "submit"), "cut to the tools both may call"
    with pytest.raises(ValidationFailed):
        await managers.agents.spawn(owner, parent.id, spawn("reporter"))
    on = managers.attribution.get_authority
    assert (await on(owner, child.id)).principal == (await on(owner, parent.id)).principal
    assert (child.parent_id, child.root_id, child.depth) == (parent.id, parent.id, 2)
    # A delegated parent's child runs under whoever asked the parent.
    assistant = await start(managers, owner, "assistant")
    (said,) = await managers.steps.append_inputs(
        teammate, assistant.id, [make_message(assistant.id)]
    )
    await model_request(managers, owner, assistant.id, [said])
    asked = await managers.agents.spawn(owner, assistant.id, spawn("assistant"))
    authority = await on(owner, asked.id)
    assert (authority.mode, authority.principal) == (AuthorityMode.DELEGATED, person_of(teammate))
    assert asked.tools == ASSISTANT.tools


async def test_a_child_made_by_hand_is_held_to_its_parent_too(managers: Managers) -> None:
    """The create is the one path every session takes, so a child sent with
    a tool its parent lacks, the wrong depth, and no mark is stored with its
    parent's; its authority names no principal its maker chose, only its
    parent's, and it pays as its parent pays."""
    org = make_org()
    ctx, asker = context(Role.MEMBER, org), context(Role.MEMBER, org)
    parent = await start(managers, ctx)
    (said,) = await managers.steps.append_inputs(asker, parent.id, [make_message(parent.id)])
    await model_request(managers, ctx, parent.id, [said])
    await managers.steps.append_inputs(
        ctx, parent.id, [make_message(parent.id).model_copy(update={"type": StepType.EVENT})]
    )
    sent = make_session(parent=parent).model_copy(
        update={"tools": ("read_log", "deploy_production"), "depth": 1, "untrusted": False}
    )
    child = await managers.agent_sessions.create_session(ctx, sent)
    assert child.tools == ("read_log",) and child.depth == 2 and child.untrusted
    authority = await managers.attribution.open_authority(ctx, child.id, AuthorityMode.DELEGATED)
    assert authority.principal == person_of(ctx), "the steady parent's principal"
    assert authority.spender == person_of(asker)


async def test_a_child_draws_on_its_trees_budget_and_deadline(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    agents = managers.agents
    root = await start(managers, ctx)
    child = await agents.spawn(ctx, root.id, spawn("delivery"))
    grandchild = await agents.spawn(ctx, child.id, spawn())
    trees = [await agents.tree_of(ctx, s.id) for s in (root, child, grandchild)]
    assert {tree.id for tree in trees} == {root.id}, "one budget scope, the root's"
    assert len({tree.deadline for tree in trees}) == 1
    later = utcnow() + timedelta(days=1)
    moved = await agents.set_deadline(ctx, grandchild.id, later)
    assert moved.id == root.id and (await agents.tree_of(ctx, root.id)).deadline == later
    with pytest.raises(ValidationError):
        Spawn.model_validate({**spawn().model_dump(), "budget": 10})
    with pytest.raises(ValidationError):
        Spawn.model_validate({**spawn().model_dump(), "deadline": later})


async def test_a_tree_stops_at_its_height_and_its_count(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    agents = managers.agents
    root = await start(managers, ctx, "assistant")
    asked = spawn("assistant")
    child = await agents.spawn(ctx, root.id, asked)
    assert await agents.spawn(ctx, root.id, asked) == child, "asked again, made once"
    with pytest.raises(TreeBoundReached):
        await agents.spawn(ctx, root.id, spawn("assistant"))
    deep = await start(managers, ctx)
    first = await agents.spawn(ctx, deep.id, spawn("delivery"))
    second = await agents.spawn(ctx, first.id, spawn("delivery"))
    with pytest.raises(TreeBoundReached):
        await agents.spawn(ctx, second.id, spawn())
    assert (await agents.tree_of(ctx, deep.id)).size == 2


async def test_cancelling_a_parent_cancels_every_session_below_it(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    agents, steps, sessions = managers.agents, managers.steps, managers.agent_sessions
    root = await start(managers, ctx)
    first = await agents.spawn(ctx, root.id, spawn("delivery"))
    second = await agents.spawn(ctx, root.id, spawn())
    grandchild = await agents.spawn(ctx, first.id, spawn())
    # The second child's loop parks; the grandchild read its objective, and
    # its loop ended: it is idle.
    epoch = await steps.begin_run(ctx, second.id)
    opening = (await steps.get_steps(ctx, second.id, 0, 1)).items[0]
    await steps.append_steps(ctx, second.id, epoch, [make_parked(second.id, opening.id)])
    assert (await sessions.project_status(ctx, second.id)).status is SessionStatus.PARKED
    (objective,) = (await steps.get_steps(ctx, grandchild.id, 0, 1)).items
    request = make_request(grandchild.id, objective.id, (objective.id,))
    response = make_response(grandchild.id, objective.id, request.id)
    epoch = await steps.begin_run(ctx, grandchild.id)
    await steps.append_steps(
        ctx, grandchild.id, epoch, [request, response, ended(grandchild.id, objective.id)]
    )
    assert (await sessions.project_status(ctx, grandchild.id)).status is SessionStatus.IDLE
    reached = await agents.cancel_children(ctx, root.id)
    assert set(reached) == {first.id, second.id}, "an idle session has nothing to cancel"
    for child in (first, second):
        last = (await steps.get_steps(ctx, child.id, 0, 10)).items[-1]
        assert isinstance(last.header, ControlHeader)
        assert last.header.command is ControlCommand.CANCEL
        assert (last.actor, last.origin) == (Actor.ENGINE, Origin.PARENT)
    assert (await sessions.project_status(ctx, second.id)).status is SessionStatus.PENDING
    assert await agents.cancel_children(ctx, grandchild.id) == ()


async def test_a_child_waiting_to_begin_its_next_loop_is_cancelled_too(
    managers: Managers,
) -> None:
    """A child whose loop ended with a waking input undelivered is pending:
    the input begins its next loop. The cascade reaches it, on the loop that
    input begins, so it never starts work after its tree was cancelled."""
    ctx = context(Role.MEMBER)
    agents, steps, sessions = managers.agents, managers.steps, managers.agent_sessions
    root = await start(managers, ctx)
    child = await agents.spawn(ctx, root.id, spawn())
    (objective,) = (await steps.get_steps(ctx, child.id, 0, 1)).items
    epoch = await steps.begin_run(ctx, child.id)
    await steps.append_steps(ctx, child.id, epoch, [ended(child.id, objective.id)])
    waiting = await sessions.project_status(ctx, child.id)
    assert waiting.status is SessionStatus.PENDING and waiting.pending_input == objective.id
    assert await agents.cancel_children(ctx, root.id) == (child.id,)
    last = (await steps.get_steps(ctx, child.id, 0, 10)).items[-1]
    assert isinstance(last.header, ControlHeader)
    assert (last.header.command, last.loop_id) == (ControlCommand.CANCEL, objective.id)


async def test_a_child_starts_from_its_objective_never_its_parents_history(
    managers: Managers,
) -> None:
    ctx = context(Role.MEMBER)
    parent = await start(managers, ctx)
    await managers.steps.append_inputs(ctx, parent.id, [make_message(parent.id)])
    child = await managers.agents.spawn(ctx, parent.id, spawn())
    (objective,) = (await managers.steps.get_steps(ctx, child.id, 0, 10)).items
    assert objective.as_text() == "run the failing test"
    assert (objective.actor, objective.origin) == (Actor.AGENT, Origin.PARENT)
    assert isinstance(objective.header, InputHeader) and objective.header.waking
    assert objective.header.agent is not None and objective.header.agent.session_id == parent.id
    assert trust_of(objective) is Trust.INSTRUCTION
    assert (await managers.agent_sessions.project_status(ctx, child.id)).status is (
        SessionStatus.PENDING
    )


async def test_a_handoff_waits_for_its_principal_and_its_objective_is_data(
    managers: Managers,
) -> None:
    org = make_org()
    owner, asker = context(Role.MEMBER, org), context(Role.MEMBER, org)
    agents, sessions = managers.agents, managers.agent_sessions
    source = await start(managers, owner, "assistant")
    (said,) = await managers.steps.append_inputs(asker, source.id, [make_message(source.id)])
    await model_request(managers, owner, source.id, [said])
    handoff = Handoff(id=new_id(), kind="delivery", title="fix it", objective="fix the total")
    handed = await agents.hand_off(owner, source.id, handoff)
    assert (handed.handed_off_from, handed.parent_id, handed.root_id) == (
        source.id,
        None,
        handed.id,
    )
    authority = await managers.attribution.get_authority(owner, handed.id)
    assert (authority.mode, authority.principal) == (AuthorityMode.STEADY, person_of(asker))
    assert authority.spender is None, "paid by the person who confirms it"
    assert handed.participants == (asker.user_id,)
    assert (await agents.tree_of(owner, handed.id)).id == handed.id
    assert await agents.hand_off(owner, source.id, handoff) == handed
    (objective,) = (await managers.steps.get_steps(owner, handed.id, 0, 10)).items
    assert trust_of(objective) is Trust.DATA
    assert (await sessions.project_status(owner, handed.id)).status is SessionStatus.IDLE
    await managers.steps.append_inputs(asker, handed.id, [make_message(handed.id)])
    assert (await sessions.project_status(asker, handed.id)).status is SessionStatus.PENDING
    with pytest.raises(ValidationFailed):
        await agents.hand_off(owner, handed.id, handoff)


async def test_a_handoff_carries_its_mark(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    source = await start(managers, ctx, "assistant")
    events = [make_message(source.id).model_copy(update={"type": StepType.EVENT})]
    await managers.steps.append_inputs(ctx, source.id, events)
    handoff = Handoff(id=new_id(), kind="delivery", title="fix it", objective="fix the total")
    handed = await managers.agents.hand_off(ctx, source.id, handoff)
    assert handed.untrusted


async def test_a_result_passes_the_gate_and_the_null_gate_marks_it_unverified(
    tmp_path: Path, managers: Managers
) -> None:
    ctx = context(Role.MEMBER)
    sid = (await start(managers, ctx)).id
    cited = Result(claim=Claim.SUCCEEDED, evidence=(new_id(),))
    refused = await managers.agents.judge_result(ctx, sid, Result(claim=Claim.SUCCEEDED))
    assert not refused.accepted and refused.reason is not None
    accepted = await managers.agents.judge_result(ctx, sid, cited)
    assert accepted == Verdict(accepted=True, verified=False, outcome=LoopOutcome.SUCCEEDED)
    failed = Result(claim=Claim.FAILED, evidence=(new_id(),))
    assert (await managers.agents.judge_result(ctx, sid, failed)).outcome is LoopOutcome.FAILED
    gated = build_managers(
        StorageMemoryImpl(), InfraLocalImpl(tmp_path), agent_kinds=KINDS, result_gate=Refusing()
    )
    gated_sid = (await start(gated, ctx)).id
    verdict = await gated.agents.judge_result(ctx, gated_sid, cited)
    assert (verdict.accepted, verdict.reason) == (False, "the cited run did not pass")


async def test_a_viewer_reads_a_tree_and_spawns_nothing(managers: Managers) -> None:
    org = make_org()
    member, viewer = context(Role.MEMBER, org), context(Role.VIEWER, org)
    root = await start(managers, member)
    assert (await managers.agents.tree_of(viewer, root.id)).id == root.id
    with pytest.raises(NotAuthorized):
        await managers.agents.spawn(viewer, root.id, spawn())
    with pytest.raises(NotAuthorized):
        await managers.agents.cancel_children(viewer, root.id)
    with pytest.raises(NotAuthorized):
        await managers.agents.set_deadline(viewer, root.id, None)
