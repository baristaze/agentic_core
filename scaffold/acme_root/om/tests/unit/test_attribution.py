"""Who stands behind each step: the rules that tell instruction from data,
fold the speaker and the mark, and choose who pays and whose authority a
call runs under; and the attribution manager over a recorded loop, with a
fake of the adopter's transition that counts every question it is asked."""

from pathlib import Path
from uuid import UUID

import pytest
from contracts.agent_session_storage import make_session
from contracts.doubles import context, model_request, next_attribution
from contracts.factories import make_org
from contracts.step_storage import (
    a_person,
    make_event,
    make_message,
    make_parked,
    make_request,
    make_response,
    make_tool_response,
)
from pydantic import ValidationError

from acme.infra.impl.local import InfraLocalImpl
from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.agents.types.kind import AgentKind, DoneRule, TreeLimits
from acme.om.agents.types.request import Spawn, Start
from acme.om.attribution.manager import PrincipalContext
from acme.om.attribution.rules import (
    call_principal,
    fold,
    marks,
    needs_person,
    principal_authored,
    said_by,
    speaker_after,
    spender_of,
    trust_of,
)
from acme.om.attribution.types.authority import (
    AuthorityMode,
    CallReach,
    SessionAuthority,
    Trust,
)
from acme.om.attribution.types.principal import AgentRef, Principal, PrincipalKind
from acme.om.base import new_id, utcnow
from acme.om.context import (
    CredentialKind,
    RequestContext,
    Role,
    TenantContext,
    build_context,
)
from acme.om.exceptions import (
    AuthorityRevoked,
    NoSpender,
    NotAuthorized,
    NotFound,
    PrincipalLapsed,
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
    MarkHeader,
    ModelRequestHeader,
    ModelResponseHeader,
    SummaryHeader,
    ToolRequestHeader,
)
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.tenancy.rules import permissions_of

SESSION = new_id()

DELIVERY = AgentKind(
    name="delivery",
    version=1,
    tools=("read_log", "run_tests", "push_branch", "spawn", "submit"),
    done_rule=DoneRule.RESULT_TOOL,
    result_tool="submit",
    authority=AuthorityMode.STEADY,
    tree=TreeLimits(height=3, count=4),
)
ASSISTANT = AgentKind(
    name="assistant",
    version=1,
    tools=("read_records", "spawn"),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    tree=TreeLimits(height=2, count=2),
)
OUTWARD = CallReach(outward=True, holds_private=True)


class Transition:
    """The adopter's transition, faked: it answers for a principal with that
    person's own context in the tenant until they are revoked, and keeps
    every question it is asked."""

    def __init__(self) -> None:
        self.asked: list[Principal] = []
        self.revoked: set[UUID] = set()
        self.answers_for: UUID | None = None  # a broken transition's other person

    async def __call__(
        self, rctx: RequestContext, org_id: UUID, principal: Principal
    ) -> TenantContext:
        self.asked.append(principal)
        if principal.id in self.revoked:
            raise NotAuthorized(f"{principal.id} holds no place in {org_id}")
        return build_context(
            rctx,
            user_id=self.answers_for or principal.id,
            org_id=org_id,
            role=Role.MEMBER,
            permissions=permissions_of(Role.MEMBER),
            credential_kind=CredentialKind.INTERNAL,
        )


def person_of(ctx: TenantContext) -> Principal:
    return Principal(kind=PrincipalKind.PERSON, id=ctx.user_id)


def a_step(step_type: StepType, header: object, **fields: object) -> Step:
    return Step.model_validate(
        {
            "id": new_id(),
            "created_at": utcnow(),
            "session_id": SESSION,
            "loop_id": new_id(),
            "type": step_type,
            "actor": Actor.ENGINE,
            "origin": Origin.ENGINE,
            "header": header,
            **fields,
        }
    )


def from_an_agent(
    session_id: UUID, principal: Principal, *, origin: Origin, untrusted: bool = False
) -> Step:
    """A message an agent wrote into `session_id`: a parent's to its child,
    or a hand-over's objective."""
    return a_step(
        StepType.MESSAGE,
        InputHeader(
            principal=principal,
            agent=AgentRef(kind="delivery", version=1, session_id=new_id()),
            untrusted=untrusted,
        ),
        session_id=session_id,
        actor=Actor.AGENT,
        origin=origin,
        content=Content(blocks=(TextBlock(text="read the nightly log"),)),
    )


def an_authority(mode: AuthorityMode, principal: Principal) -> SessionAuthority:
    now = utcnow()
    return SessionAuthority(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=principal.id,
        updated_by=principal.id,
        mode=mode,
        principal=principal,
    )


def from_a_program(session_id: UUID, principal: Principal) -> Step:
    """An automation's trigger: a program speaking for its principal."""
    return make_message(session_id, principal=principal).model_copy(
        update={"actor": Actor.PROGRAM, "origin": Origin.AUTOMATION}
    )


# The rules.


def test_only_a_principals_message_instructs() -> None:
    person = make_message(SESSION)
    program = from_a_program(SESSION, a_person())
    parent = from_an_agent(SESSION, a_person(), origin=Origin.PARENT)
    handed = from_an_agent(SESSION, a_person(), origin=Origin.ENGINE)
    summary = a_step(StepType.SUMMARY, SummaryHeader(first_seq=1, last_seq=4))
    notice = a_step(StepType.ENVIRONMENT_CHANGED, MarkHeader())
    request = make_request(SESSION, new_id())
    response = make_response(SESSION, new_id(), request.id)
    tiers = {
        "a person's message": (person, Trust.INSTRUCTION),
        "an automation's trigger": (program, Trust.INSTRUCTION),
        "a parent's message to its child": (parent, Trust.INSTRUCTION),
        "the engine's notice": (notice, Trust.INSTRUCTION),
        "an agent's message from no parent": (handed, Trust.DATA),
        "an event from outside": (make_event(SESSION), Trust.DATA),
        "a tool's output": (make_tool_response(SESSION, new_id(), new_id()), Trust.DATA),
        "a summary of the window": (summary, Trust.DATA),
        "the model's request": (request, None),
        "the model's response": (response, None),
        "a control": (a_step(StepType.CONTROL, ControlHeader(command=ControlCommand.PAUSE)), None),
        "a park": (make_parked(SESSION, new_id()), None),
    }
    assert {what: trust_of(step) for what, (step, _) in tiers.items()} == {
        what: tier for what, (_, tier) in tiers.items()
    }
    assert [principal_authored(step) for step in (person, program, parent, handed)] == [
        True,
        True,
        False,
        False,
    ]


def test_the_mark_is_set_by_the_first_data_and_never_cleared() -> None:
    asker = a_person()
    said = [make_message(SESSION, principal=asker), make_request(SESSION, new_id(), speaker=asker)]
    assert fold(None, False, said) == (asker, False)
    data = make_tool_response(SESSION, new_id(), new_id())
    after = [*said, data, make_message(SESSION, principal=asker)]
    assert fold(None, False, after) == (asker, True)
    assert fold(asker, True, [make_message(SESSION, principal=asker)]) == (asker, True)
    carried = from_an_agent(SESSION, asker, origin=Origin.PARENT, untrusted=True)
    assert trust_of(carried) is Trust.INSTRUCTION and marks(carried)
    assert not marks(from_an_agent(SESSION, asker, origin=Origin.PARENT))


def test_a_request_records_the_latest_principal_who_spoke_and_nothing_after_moves_it() -> None:
    first, routed, agents, automation = a_person(), a_person(), a_person(), a_person()
    steps = [
        make_message(SESSION, principal=first),
        make_event(SESSION, principal=routed),
        from_an_agent(SESSION, agents, origin=Origin.PARENT),
    ]
    assert speaker_after(None, steps) == first, "an event or an agent never speaks"
    automated = [*steps, from_a_program(SESSION, automation)]
    assert speaker_after(None, automated) == automation
    assert speaker_after(first, []) == first
    request = make_request(SESSION, new_id(), speaker=first)
    late = make_message(SESSION, principal=automation)
    assert fold(None, False, [request, late]) == (first, False), "unread, it moves nothing"
    spawned = a_person()
    assert spender_of(spawned, None) == spawned
    assert spender_of(spawned, first) == first
    assert spender_of(None, None) is None


def test_a_message_is_said_in_the_name_of_the_context_that_appends_it() -> None:
    claimed, appender = a_person(), new_id()
    said = said_by(make_message(SESSION, principal=claimed), appender)
    assert isinstance(said.header, InputHeader) and said.header.principal.id == appender
    event = make_event(SESSION, principal=claimed)
    assert said_by(event, appender) == event, "an event is routed, never said"
    parent = from_an_agent(SESSION, claimed, origin=Origin.PARENT)
    assert said_by(parent, appender) == parent


def test_a_call_runs_under_the_fixed_principal_or_the_latest_asker() -> None:
    fixed, asker = a_person(), a_person()
    steady = an_authority(AuthorityMode.STEADY, fixed)
    delegated = an_authority(AuthorityMode.DELEGATED, fixed)
    assert call_principal(steady, asker) == fixed
    assert call_principal(delegated, asker) == asker
    assert call_principal(delegated, None) == fixed


@pytest.mark.parametrize("marked", [True, False])
@pytest.mark.parametrize("holds_private", [True, False])
@pytest.mark.parametrize("outward", [True, False])
def test_the_rule_of_two_asks_a_person_only_when_all_three_hold(
    marked: bool, holds_private: bool, outward: bool
) -> None:
    reach = CallReach(outward=outward, holds_private=holds_private)
    assert needs_person(marked=marked, reach=reach) == (marked and holds_private and outward)


def test_a_model_request_names_its_spender_or_is_never_made() -> None:
    with pytest.raises(ValidationError):
        ModelRequestHeader.model_validate({"role": "main"})
    with pytest.raises(ValidationError):
        InputHeader.model_validate({"waking": True})


# The manager.


@pytest.fixture
def transition() -> Transition:
    return Transition()


@pytest.fixture
def managers(tmp_path: Path, transition: Transition) -> Managers:
    asked: PrincipalContext = transition
    return build_managers(
        StorageMemoryImpl(),
        InfraLocalImpl(tmp_path),
        agent_kinds=(DELIVERY, ASSISTANT),
        principal_context=asked,
    )


async def start(managers: Managers, ctx: TenantContext, kind: AgentKind) -> AgentSession:
    return await managers.agents.start_session(
        ctx, Start(id=new_id(), kind=kind.name, title="the weekly report is missing a total")
    )


async def say(
    managers: Managers, ctx: TenantContext, session_id: UUID, step: Step | None = None
) -> Step:
    """An input the person `ctx` holds appends: a message of theirs unless
    another step is given."""
    (said,) = await managers.steps.append_inputs(
        ctx, session_id, [step or make_message(session_id)]
    )
    return said


async def test_a_recorded_loop_names_who_acted_on_whose_authority_and_who_paid(
    managers: Managers, transition: Transition
) -> None:
    """A loop as the engine records it, each attribution asked of the
    manager where the loop asks it: the history names the actor of every
    step, the principal of every input and tool call, and the speaker and
    the spender of every model request."""
    ctx = context(Role.MEMBER)
    owner = person_of(ctx)
    session = await start(managers, ctx, DELIVERY)
    sid, steps, attribution = session.id, managers.steps, managers.attribution
    asked = await say(managers, ctx, sid)
    loop = asked.id
    epoch = await steps.begin_run(ctx, sid)
    said = await attribution.attribute_request(ctx, sid, 0, asked.seq)
    request = make_request(sid, loop, (asked.id,), spender=said.spender, speaker=said.speaker)
    use = ToolUseBlock(id="call_1", name="read_log", input={"lines": [1, 200]})
    response = Step(
        id=new_id(),
        created_at=utcnow(),
        session_id=sid,
        loop_id=loop,
        type=StepType.MODEL_RESPONSE,
        actor=Actor.MODEL,
        origin=Origin.ENGINE,
        responds_to=request.id,
        header=ModelResponseHeader(),
        content=Content(blocks=(use,)),
    )
    stored, _ = await steps.append_steps(ctx, sid, epoch, [request, response])
    authority = await attribution.authorize_call(ctx, sid, OUTWARD)
    call = Step(
        id=new_id(),
        created_at=utcnow(),
        session_id=sid,
        loop_id=loop,
        type=StepType.TOOL_REQUEST,
        actor=Actor.AGENT,
        origin=Origin.ENGINE,
        refs=(response.id,),
        header=ToolRequestHeader(
            tool=use.name,
            tool_use_id=use.id,
            input_hash="h:1",
            principal=authority.principal,
            authority=authority.mode,
            agent=AgentRef(kind=session.kind, version=session.kind_version, session_id=sid),
            authorization_class="integration",
        ),
    )
    result = make_tool_response(sid, loop, call.id)
    await steps.append_steps(ctx, sid, epoch, [call, result])
    routed = a_person()
    event = await say(managers, ctx, sid, make_event(sid, principal=routed))
    again = await attribution.attribute_request(ctx, sid, stored.seq, event.seq)
    second = make_request(
        sid, loop, (result.id, event.id), spender=again.spender, speaker=again.speaker
    )
    answer = make_response(sid, loop, second.id)
    ended = Step(
        id=new_id(),
        created_at=utcnow(),
        session_id=sid,
        loop_id=loop,
        type=StepType.LOOP_ENDED,
        actor=Actor.ENGINE,
        origin=Origin.ENGINE,
        header=LoopEndedHeader(outcome=LoopOutcome.SUCCEEDED),
    )
    await steps.append_steps(ctx, sid, epoch, [second, answer, ended])

    history = (await steps.get_steps(ctx, sid, 0, 50)).items
    assert [s.actor for s in history] == [
        Actor.PERSON,
        Actor.ENGINE,
        Actor.MODEL,
        Actor.AGENT,
        Actor.ENGINE,
        Actor.EXTERNAL,
        Actor.ENGINE,
        Actor.MODEL,
        Actor.ENGINE,
    ]
    principals = {
        s.type: s.header.principal
        for s in history
        if isinstance(s.header, InputHeader | ToolRequestHeader)
    }
    assert principals == {
        StepType.MESSAGE: owner,
        StepType.TOOL_REQUEST: owner,
        StepType.EVENT: routed,
    }
    requests = [s.header for s in history if isinstance(s.header, ModelRequestHeader)]
    assert [(r.speaker, r.spender) for r in requests] == [(owner, owner), (owner, owner)], (
        "the event never pays; the person who asked does"
    )
    (acted,) = (s for s in history if isinstance(s.header, ToolRequestHeader))
    assert isinstance(acted.header, ToolRequestHeader)
    assert acted.header.agent == AgentRef(kind="delivery", version=1, session_id=sid)
    assert acted.header.authority is AuthorityMode.STEADY
    assert transition.asked == [owner]


async def test_the_person_who_asked_pays_and_nobody_else_ever_does(
    managers: Managers,
) -> None:
    org = make_org()
    owner, teammate, automation = (context(Role.MEMBER, org) for _ in range(3))
    sid = (await start(managers, owner, DELIVERY)).id
    attribution = managers.attribution
    with pytest.raises(NoSpender):
        await next_attribution(managers, owner, sid)
    await say(managers, owner, sid, make_event(sid, principal=a_person()))
    with pytest.raises(NoSpender):
        await next_attribution(managers, owner, sid)
    first = await say(managers, owner, sid)
    paid = await next_attribution(managers, owner, sid)
    assert (paid.speaker, paid.spender) == (person_of(owner), person_of(owner))
    await model_request(managers, owner, sid, [first])
    await say(managers, owner, sid, make_event(sid, principal=a_person()))
    await say(managers, owner, sid, from_an_agent(sid, a_person(), origin=Origin.PARENT))
    carried = await next_attribution(managers, owner, sid)
    assert carried.spender == person_of(owner), "the current spender carries over"
    await managers.agent_sessions.project_status(owner, sid)
    await say(managers, teammate, sid)
    assert (await next_attribution(managers, owner, sid)).spender == (person_of(teammate))
    trigger = await say(managers, automation, sid, from_a_program(sid, a_person()))
    assert (await next_attribution(managers, owner, sid)).spender == (person_of(automation)), (
        "an automation's trigger is paid by the automation's principal"
    )
    with pytest.raises(ValidationFailed):
        await attribution.attribute_request(owner, sid, first.seq, trigger.seq)
    with pytest.raises(ValidationFailed):
        await attribution.attribute_request(owner, sid, 0, trigger.seq)


async def test_a_message_naming_another_is_said_in_its_appenders_name(
    managers: Managers, transition: Transition
) -> None:
    """A member who writes a message naming an admin speaks as themselves:
    they pay for the request that reads it, and the delegated call it leads
    to runs on their own live context, never the admin's."""
    org = make_org()
    member, admin = context(Role.MEMBER, org), context(Role.ADMIN, org)
    sid = (await start(managers, member, ASSISTANT)).id
    named = make_message(sid, principal=person_of(admin))
    said = await say(managers, member, sid, named)
    assert isinstance(said.header, InputHeader) and said.header.principal == person_of(member)
    paid = await next_attribution(managers, member, sid)
    assert paid.spender == person_of(member)
    await model_request(managers, member, sid, [said])
    call = await managers.attribution.authorize_call(member, sid, OUTWARD)
    assert call.principal == person_of(member) and call.context.user_id == member.user_id
    assert transition.asked == [person_of(member)]
    epoch = await managers.steps.begin_run(member, sid)
    (run_said,) = await managers.steps.append_steps(member, sid, epoch, [named])
    assert run_said.header == said.header, "a run's append writes it in the same name"


async def test_a_message_after_the_request_lends_its_authority_to_nothing(
    managers: Managers, transition: Transition
) -> None:
    """The calls and spawns a response leads to run under the speaker the
    request recorded. An admin's message appended after the response is
    no part of what the model read, and lends the admin's context to none
    of them."""
    org = make_org()
    member, admin = context(Role.MEMBER, org), context(Role.ADMIN, org)
    sid = (await start(managers, member, ASSISTANT)).id
    asked = await say(managers, member, sid)
    await model_request(managers, member, sid, [asked])
    await say(managers, admin, sid)
    call = await managers.attribution.authorize_call(member, sid, OUTWARD)
    assert call.principal == person_of(member) and transition.asked == [person_of(member)]
    child = await managers.agents.spawn(
        member,
        sid,
        Spawn(id=new_id(), kind="assistant", title="look", objective="read the records"),
    )
    child_authority = await managers.attribution.get_authority(member, child.id)
    assert child_authority.principal == person_of(member) != person_of(admin)


async def test_a_child_pays_as_its_spawn_did_until_a_principal_speaks_to_it(
    managers: Managers,
) -> None:
    org = make_org()
    owner, asker, steering = (context(Role.MEMBER, org) for _ in range(3))
    parent = await start(managers, owner, DELIVERY)
    asked = await say(managers, asker, parent.id)
    await model_request(managers, owner, parent.id, [asked])
    child = await managers.agents.spawn(
        owner,
        parent.id,
        Spawn(id=new_id(), kind="delivery", title="reproduce it", objective="run the tests"),
    )
    authority = await managers.attribution.get_authority(owner, child.id)
    assert (authority.principal, authority.spender) == (person_of(owner), person_of(asker))
    paid = await next_attribution(managers, owner, child.id)
    assert paid.spender == person_of(asker), "a parent's message never pays"
    await say(managers, steering, child.id)
    paid = await next_attribution(managers, owner, child.id)
    assert paid.spender == person_of(steering)


async def test_the_mark_is_sticky_from_the_first_data_and_passes_to_children(
    managers: Managers,
) -> None:
    ctx = context(Role.MEMBER)
    attribution = managers.attribution
    parent = await start(managers, ctx, DELIVERY)
    await say(managers, ctx, parent.id)
    assert not await attribution.is_marked(ctx, parent.id)
    clean = await managers.agents.spawn(
        ctx, parent.id, Spawn(id=new_id(), kind="delivery", title="early", objective="look")
    )
    assert not clean.untrusted and not await attribution.is_marked(ctx, clean.id)
    await say(managers, ctx, parent.id, make_event(parent.id))
    assert await attribution.is_marked(ctx, parent.id)
    cached = await managers.agent_sessions.project_status(ctx, parent.id)
    assert cached.untrusted
    await say(managers, ctx, parent.id)
    assert await attribution.is_marked(ctx, parent.id), "nothing clears it"
    late = await managers.agents.spawn(
        ctx, parent.id, Spawn(id=new_id(), kind="delivery", title="late", objective="look")
    )
    assert late.untrusted and await attribution.is_marked(ctx, late.id)
    objective = (await managers.steps.get_steps(ctx, late.id, 0, 10)).items[0]
    assert isinstance(objective.header, InputHeader) and objective.header.untrusted
    # A marked parent's later message marks the child it reaches.
    on = (await attribution.get_authority(ctx, clean.id)).principal
    await say(
        managers, ctx, clean.id, from_an_agent(clean.id, on, origin=Origin.PARENT, untrusted=True)
    )
    assert await attribution.is_marked(ctx, clean.id)


async def test_a_marked_session_holding_private_data_needs_a_person_to_act_outward(
    managers: Managers,
) -> None:
    ctx = context(Role.MEMBER)
    attribution = managers.attribution
    sid = (await start(managers, ctx, DELIVERY)).id
    await say(managers, ctx, sid)
    inward = CallReach(outward=False, holds_private=True)
    nothing_private = CallReach(outward=True, holds_private=False)
    assert not (await attribution.authorize_call(ctx, sid, OUTWARD)).needs_person
    await say(managers, ctx, sid, make_event(sid))
    assert (await attribution.authorize_call(ctx, sid, OUTWARD)).needs_person
    assert not (await attribution.authorize_call(ctx, sid, inward)).needs_person
    assert not (await attribution.authorize_call(ctx, sid, nothing_private)).needs_person


async def test_delegated_authority_asks_the_transition_again_on_every_call(
    managers: Managers, transition: Transition
) -> None:
    org = make_org()
    owner, asker, other = (context(Role.MEMBER, org) for _ in range(3))
    attribution = managers.attribution
    sid = (await start(managers, owner, ASSISTANT)).id
    await model_request(managers, owner, sid, [await say(managers, asker, sid)])
    first = await attribution.authorize_call(owner, sid, OUTWARD)
    second = await attribution.authorize_call(owner, sid, OUTWARD)
    assert transition.asked == [person_of(asker)] * 2, "asked on each call, never once per loop"
    assert first.mode is AuthorityMode.DELEGATED and first.principal == person_of(asker)
    assert (second.context.user_id, second.context.org_id) == (asker.user_id, org.id)
    transition.revoked.add(asker.user_id)
    with pytest.raises(AuthorityRevoked):
        await attribution.authorize_call(owner, sid, OUTWARD)
    assert transition.asked == [person_of(asker)] * 3
    await model_request(managers, owner, sid, [await say(managers, other, sid)])
    assert (await attribution.authorize_call(owner, sid, OUTWARD)).principal == person_of(other)


async def test_a_steady_session_runs_under_its_principal_and_parks_when_it_lapses(
    managers: Managers, transition: Transition
) -> None:
    org = make_org()
    owner_ctx, teammate_ctx = context(Role.MEMBER, org), context(Role.MEMBER, org)
    owner, teammate = person_of(owner_ctx), person_of(teammate_ctx)
    attribution = managers.attribution
    sid = (await start(managers, owner_ctx, DELIVERY)).id
    steered = await say(managers, teammate_ctx, sid)
    await model_request(managers, owner_ctx, sid, [steered])
    held = await attribution.authorize_call(teammate_ctx, sid, OUTWARD)
    assert held.principal == owner and held.context.user_id == owner.id
    transition.revoked.add(owner.id)
    with pytest.raises(PrincipalLapsed):
        await attribution.authorize_call(teammate_ctx, sid, OUTWARD)
    taken = await attribution.assign_principal(teammate_ctx, sid)
    assert (taken.mode, taken.principal, taken.version) == (AuthorityMode.STEADY, teammate, 2)
    assert (await attribution.authorize_call(teammate_ctx, sid, OUTWARD)).principal == teammate


async def test_a_transition_that_answers_for_someone_else_answers_nothing(
    managers: Managers, transition: Transition
) -> None:
    ctx = context(Role.MEMBER)
    sid = (await start(managers, ctx, ASSISTANT)).id
    await model_request(managers, ctx, sid, [await say(managers, ctx, sid)])
    transition.answers_for = new_id()
    with pytest.raises(AuthorityRevoked):
        await managers.attribution.authorize_call(ctx, sid, OUTWARD)


async def test_with_no_transition_wired_no_call_runs(tmp_path: Path) -> None:
    managers = build_managers(
        StorageMemoryImpl(), InfraLocalImpl(tmp_path), agent_kinds=(DELIVERY, ASSISTANT)
    )
    ctx = context(Role.MEMBER)
    delegated = (await start(managers, ctx, ASSISTANT)).id
    steady = (await start(managers, ctx, DELIVERY)).id
    with pytest.raises(AuthorityRevoked):
        await managers.attribution.authorize_call(ctx, delegated, OUTWARD)
    with pytest.raises(PrincipalLapsed):
        await managers.attribution.authorize_call(ctx, steady, OUTWARD)


async def test_a_viewer_reads_attribution_and_takes_over_nothing(managers: Managers) -> None:
    org = make_org()
    member, viewer = context(Role.MEMBER, org), context(Role.VIEWER, org)
    sid = (await start(managers, member, DELIVERY)).id
    await model_request(managers, member, sid, [await say(managers, member, sid)])
    paid = await next_attribution(managers, viewer, sid)
    assert paid.spender == person_of(member)
    with pytest.raises(NotAuthorized):
        await managers.attribution.authorize_call(viewer, sid, OUTWARD)
    with pytest.raises(NotAuthorized):
        await managers.attribution.assign_principal(viewer, sid)


async def test_a_session_with_no_authority_runs_no_call_and_spends_nothing(
    managers: Managers,
) -> None:
    """A session made around the agents swimlane has no authority until one
    is opened for it, and nothing runs on it meanwhile; a session made from
    one that holds none gets none, and only its maker opens one."""
    org = make_org()
    ctx, other = context(Role.MEMBER, org), context(Role.MEMBER, org)
    bare = await managers.agent_sessions.create_session(ctx, make_session())
    await say(managers, ctx, bare.id)
    with pytest.raises(NotFound):
        await managers.attribution.authorize_call(ctx, bare.id, OUTWARD)
    with pytest.raises(NotFound):
        await next_attribution(managers, ctx, bare.id)
    orphan = await managers.agent_sessions.create_session(ctx, make_session(parent=bare))
    with pytest.raises(ValidationFailed):
        await managers.attribution.open_authority(ctx, orphan.id, AuthorityMode.STEADY)
    with pytest.raises(NotAuthorized):
        await managers.attribution.open_authority(other, bare.id, AuthorityMode.STEADY)
    opened = await managers.attribution.open_authority(ctx, bare.id, AuthorityMode.DELEGATED)
    assert opened.principal == person_of(ctx) and opened.spender is None
    again = await managers.attribution.open_authority(ctx, bare.id, AuthorityMode.STEADY)
    assert again == opened, "made once, answered as stored"
