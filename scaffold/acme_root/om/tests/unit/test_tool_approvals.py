"""An approval is a person's decision on one exact call: the tool and its
input's hash. A changed input is a new call and asks again; an approval that
expired asks again; a denial answers the call `denied` with the person's
note. Only a person whose role the tenant lets approve the call's class
decides it."""

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.doubles import context
from contracts.factories import make_org
from contracts.tools import (
    KIND_DEFAULTS,
    PushBranch,
    Tools,
    failure_of,
    put_call,
    registry_of,
    result_text,
    tools_over,
    twin_transport,
)
from pydantic import ValidationError

from acme.infra.workspaces import Workspace
from acme.om.base import new_id, utcnow
from acme.om.context import CredentialKind, Role, build_context
from acme.om.exceptions import NotAuthorized, NotFound, ValidationFailed
from acme.om.steps.types.content import Content, TextBlock
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    DecidedCall,
    ToolFailure,
    ToolRequestHeader,
)
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.tenancy.rules import permissions_of
from acme.om.tenancy.types.org import Org
from acme.om.tools.registry import ToolRegistry
from acme.om.tools.rules import decides, verdict
from acme.om.tools.types.call import GateOutcome, Verdict
from acme.om.tools.types.policy import ApproverRule, Decision

PROTECTIONS = {"main": True, "feature": False, "hotfix": False}
OWNERS = (Role.OWNER, Role.ADMIN)


@dataclass
class Setting:
    tools: Tools
    org: Org
    push: PushBranch
    registry: ToolRegistry


@pytest.fixture
def setting(tmp_path: Path) -> Setting:
    transport, _ = twin_transport(tmp_path)
    push = PushBranch(PROTECTIONS)
    return Setting(tools_over(transport), make_org(), push, registry_of(push))


async def test_an_approval_binds_the_call_and_a_changed_input_asks_again(setting: Setting) -> None:
    tools, org, push, registry = setting.tools, setting.org, setting.push, setting.registry
    agent, owner = context(Role.SERVICE, org), context(Role.OWNER, org)
    workspace = Workspace.absent(org.id, new_id())
    first = await put_call(
        tools.manager, tools.steps, agent, "push_branch", {"branch": "feature"}, "integration"
    )
    gate = await tools.manager.gate(
        agent, registry, KIND_DEFAULTS, first.request, first.call_input, workspace
    )
    assert gate.outcome is GateOutcome.ASK and gate.decision is Decision.APPROVE

    decision = await tools.manager.decide_call(
        owner, first.session_id, first.request.seq, approve=True
    )
    header = decision.header
    assert isinstance(header, ControlHeader) and header.call is not None
    assert decision.actor is Actor.PERSON and decision.refs == (first.request.id,)
    assert header.call.tool == "push_branch"
    bound = await tools.manager.input_hash(agent, first.session_id, {"branch": "feature"})
    assert header.call.input_hash == bound
    assert header.call.decided_by == owner.user_id

    gate = await tools.manager.gate(
        agent, registry, KIND_DEFAULTS, first.request, first.call_input, workspace
    )
    assert gate.outcome is GateOutcome.RUN

    # The same tool with another input is another call, in the same loop.
    changed = await put_call(
        tools.manager,
        tools.steps,
        agent,
        "push_branch",
        {"branch": "hotfix"},
        "integration",
        session_id=first.session_id,
        epoch=first.epoch,
    )
    gate = await tools.manager.gate(
        agent, registry, KIND_DEFAULTS, changed.request, changed.call_input, workspace
    )
    assert gate.outcome is GateOutcome.ASK, "the approval of one input is not one of another"

    # An input that is not the one the request recorded is never run on it.
    with pytest.raises(ValidationFailed, match="not the one request"):
        await tools.manager.gate(
            agent, registry, KIND_DEFAULTS, first.request, changed.call_input, workspace
        )
    assert push.pushed == [], "a gate runs nothing"


async def test_an_expired_approval_asks_again(setting: Setting) -> None:
    tools, org, registry = setting.tools, setting.org, setting.registry
    agent, owner = context(Role.SERVICE, org), context(Role.OWNER, org)
    workspace = Workspace.absent(org.id, new_id())
    found = await put_call(
        tools.manager, tools.steps, agent, "push_branch", {"branch": "feature"}, "integration"
    )
    await tools.manager.decide_call(owner, found.session_id, found.request.seq, approve=True)
    tools.clock.now += timedelta(hours=1, seconds=1)
    gate = await tools.manager.gate(
        agent, registry, KIND_DEFAULTS, found.request, found.call_input, workspace
    )
    assert gate.outcome is GateOutcome.ASK
    # Asked again, a new approval lets it run.
    await tools.manager.decide_call(owner, found.session_id, found.request.seq, approve=True)
    gate = await tools.manager.gate(
        agent, registry, KIND_DEFAULTS, found.request, found.call_input, workspace
    )
    assert gate.outcome is GateOutcome.RUN


async def test_a_denial_is_a_denied_tool_response_with_the_persons_note(setting: Setting) -> None:
    tools, org, push, registry = setting.tools, setting.org, setting.push, setting.registry
    agent, owner = context(Role.SERVICE, org), context(Role.OWNER, org)
    workspace = Workspace.absent(org.id, new_id())
    found = await put_call(
        tools.manager, tools.steps, agent, "push_branch", {"branch": "feature"}, "integration"
    )
    await tools.manager.decide_call(
        owner, found.session_id, found.request.seq, approve=False, note="not during the freeze"
    )
    gate = await tools.manager.gate(
        agent, registry, KIND_DEFAULTS, found.request, found.call_input, workspace
    )
    assert gate.outcome is GateOutcome.REFUSE and gate.response is not None
    response = gate.response
    assert failure_of(response) is ToolFailure.DENIED
    assert response.responds_to == found.request.id
    assert response.as_tool_response().is_error
    assert "not during the freeze" in result_text(response)
    assert push.pushed == []


async def test_only_an_approver_of_the_class_decides(setting: Setting) -> None:
    tools, org = setting.tools, setting.org
    agent = context(Role.SERVICE, org)
    member, admin = context(Role.MEMBER, org), context(Role.ADMIN, org)
    found = await put_call(
        tools.manager, tools.steps, agent, "push_branch", {"branch": "feature"}, "integration"
    )
    for nobody in (member, context(Role.VIEWER, org), agent):
        with pytest.raises(NotAuthorized):
            await tools.manager.decide_call(
                nobody, found.session_id, found.request.seq, approve=True
            )
    policy = await tools.manager.get_policy(admin)
    approvers = (ApproverRule(authorization_class="integration", roles=(Role.MEMBER,)),)
    await tools.manager.write_policy(admin, policy.model_copy(update={"approvers": approvers}))
    await tools.manager.decide_call(member, found.session_id, found.request.seq, approve=True)
    with pytest.raises(NotAuthorized):
        await tools.manager.decide_call(admin, found.session_id, found.request.seq, approve=True)
    with pytest.raises(NotFound):
        await tools.manager.decide_call(
            member, found.session_id, found.request.seq - 1, approve=True
        )


def decision_on(
    request: Step,
    *,
    actor: Actor = Actor.PERSON,
    refs: tuple[UUID, ...] | None = None,
    tool: str = "push_branch",
    hash_: str | None = None,
    command: ControlCommand = ControlCommand.APPROVE,
    role: Role = Role.OWNER,
) -> Step:
    header = request.header
    assert isinstance(header, ToolRequestHeader)
    now = utcnow()
    return Step(
        id=new_id(),
        created_at=now,
        session_id=request.session_id,
        loop_id=request.loop_id,
        type=StepType.CONTROL,
        actor=actor,
        origin=Origin.PORTAL,
        refs=(request.id,) if refs is None else refs,
        header=ControlHeader(
            command=command,
            call=DecidedCall(
                tool=tool,
                input_hash=hash_ or header.input_hash,
                decided_by=new_id(),
                role=role,
                expires_at=now + timedelta(hours=1) if command is ControlCommand.APPROVE else None,
            ),
        ),
        content=Content(blocks=(TextBlock(text="ok"),)),
    )


async def test_only_a_persons_decision_on_exactly_this_call_counts(setting: Setting) -> None:
    tools, org = setting.tools, setting.org
    agent = context(Role.SERVICE, org)
    found = await put_call(
        tools.manager, tools.steps, agent, "push_branch", {"branch": "feature"}, "integration"
    )
    request, now = found.request, utcnow()
    main = await tools.manager.input_hash(agent, found.session_id, {"branch": "main"})
    assert verdict(request, [decision_on(request)], now, OWNERS)[0] is Verdict.APPROVED
    for stray in (
        decision_on(request, actor=Actor.ENGINE),
        decision_on(request, actor=Actor.MODEL),
        decision_on(request, refs=(new_id(),)),
        decision_on(request, tool="delete_branch"),
        decision_on(request, hash_=main),
        decision_on(request, role=Role.MEMBER),
    ):
        assert verdict(request, [stray], now, OWNERS)[0] is Verdict.PENDING
    # An agent's control step names its agent, so none decides a call.
    with pytest.raises(ValidationError):
        decision_on(request, actor=Actor.AGENT)
    # The latest decision holds.
    approve, deny = decision_on(request), decision_on(request, command=ControlCommand.DENY)
    assert verdict(request, [approve, deny], now, OWNERS)[0] is Verdict.DENIED
    assert verdict(request, [deny, approve], now, OWNERS)[0] is Verdict.APPROVED


async def test_a_decision_counts_only_in_a_role_the_policy_lets_decide(setting: Setting) -> None:
    """However a decision reaches the history, it is its appender's, in the
    role they hold, and it lets the call run only while the policy lets
    that role decide the call's class."""
    tools, org, registry = setting.tools, setting.org, setting.registry
    agent, member = context(Role.SERVICE, org), context(Role.MEMBER, org)
    owner, admin = context(Role.OWNER, org), context(Role.ADMIN, org)
    workspace = Workspace.absent(org.id, new_id())
    found = await put_call(
        tools.manager, tools.steps, agent, "push_branch", {"branch": "feature"}, "integration"
    )

    async def outcome() -> GateOutcome:
        gate = await tools.manager.gate(
            agent, registry, KIND_DEFAULTS, found.request, found.call_input, workspace
        )
        return gate.outcome

    (stored,) = await tools.steps.append_inputs(
        member, found.session_id, [decision_on(found.request)]
    )
    header = stored.header
    assert isinstance(header, ControlHeader) and header.call is not None
    assert (header.call.decided_by, header.call.role) == (member.user_id, Role.MEMBER)
    assert await outcome() is GateOutcome.ASK, "a member's approval is none"
    await tools.manager.decide_call(owner, found.session_id, found.request.seq, approve=True)
    assert await outcome() is GateOutcome.RUN
    policy = await tools.manager.get_policy(admin)
    admins = (ApproverRule(authorization_class="integration", roles=(Role.ADMIN,)),)
    await tools.manager.write_policy(admin, policy.model_copy(update={"approvers": admins}))
    assert await outcome() is GateOutcome.ASK, "the owner no longer decides this class"


async def test_a_decision_sent_on_an_api_key_is_a_programs_and_approves_nothing(
    setting: Setting,
) -> None:
    """An owner's API key may send a decision, and the history records it as
    the program's: no verdict counts it, so the call still waits for a
    person."""
    tools, org, registry = setting.tools, setting.org, setting.registry
    agent, owner = context(Role.SERVICE, org), context(Role.OWNER, org)
    program = build_context(
        owner,
        user_id=owner.user_id,
        org_id=org.id,
        role=Role.OWNER,
        permissions=permissions_of(Role.OWNER),
        credential_kind=CredentialKind.API_KEY,
    )
    workspace = Workspace.absent(org.id, new_id())
    found = await put_call(
        tools.manager, tools.steps, agent, "push_branch", {"branch": "feature"}, "integration"
    )

    decision = await tools.manager.decide_call(
        program, found.session_id, found.request.seq, approve=True
    )
    gate = await tools.manager.gate(
        agent, registry, KIND_DEFAULTS, found.request, found.call_input, workspace
    )

    assert decision.actor is Actor.PROGRAM
    assert not decides(decision, found.request, OWNERS)
    assert gate.outcome is GateOutcome.ASK
