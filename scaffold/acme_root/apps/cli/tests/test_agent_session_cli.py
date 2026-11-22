"""A session from the terminal, against the whole API in-process: started,
spoken to, steered, and read one step a line; and `steps --follow`, which
reads until the loop stops and exits by how it ended. No runner works
behind the API here, so a case writes the loop's steps itself, the way a
run writes them."""

import json
from uuid import UUID

from api_support import OWNER, run, seed_request
from cli_support import Stack

from acme.apps.cli import main
from acme.om.agent_steps.types.header import LoopOutcome, Park, ParkReason, ToolFailure
from acme.om.agents.loop_rules import NUDGE, ended_step, notice_step
from acme.om.base import new_id, utcnow
from acme.om.context import TenantContext
from acme.om.tools.rules import ADVICE, response

from contracts.agent_step_storage import a_person, make_request, make_response, make_tool_request


def owner_context(stack: Stack) -> TenantContext:
    tenancy = stack.container.managers.tenancy
    return run(tenancy.authenticate(seed_request(), stack.auth_session_token(OWNER["email"])))


def started(stack: Stack) -> str:
    result = stack.acme("agent-session", "start", "assistant", "the dropped object")
    assert result.exit_code == 0, result.output
    assert result.output.startswith("started ") and result.output.endswith(" on assistant 1\n")
    return result.output.split()[1]


APPROVAL = Park(reason=ParkReason.PERSON, unlock="approval")


def a_loop_ran(
    stack: Stack,
    agent_session_id: str,
    outcome: LoopOutcome | None,
    *,
    park: Park = APPROVAL,
    cause: str | None = None,
) -> None:
    """A run's steps: its request delivered the message and was answered,
    then the loop ended with `outcome` and `cause`, or parked on `park` with
    None."""
    ctx = owner_context(stack)
    sid = UUID(agent_session_id)
    managers = stack.container.managers

    async def write() -> None:
        epoch = await managers.agent_steps.begin_run(ctx, sid)
        (message,) = (await managers.agent_steps.get_agent_steps(ctx, sid, 0, 10)).items
        request = make_request(sid, message.loop_id, (message.id,))
        response = make_response(sid, message.loop_id, request.id)
        await managers.agent_steps.append_agent_steps(ctx, sid, epoch, [request, response])
        if outcome is None:
            await managers.agent_sessions.park(ctx, sid, epoch, message.loop_id, park)
            return
        ended = ended_step(new_id(), utcnow(), sid, message.loop_id, outcome, cause=cause)
        await managers.agent_steps.append_agent_steps(ctx, sid, epoch, [ended])
        await managers.agent_sessions.project_status(ctx, sid)

    run(write())


def test_a_session_is_started_spoken_to_steered_and_read(stack: Stack) -> None:
    agent_session_id = started(stack)

    said = stack.acme("agent-session", "say", agent_session_id, "Why does it drop the object?")
    paused = stack.acme("agent-session", "control", agent_session_id, "pause")
    read = stack.acme("agent-session", "steps", agent_session_id)

    assert said.exit_code == 0 and paused.exit_code == 0, said.output + paused.output
    assert said.output.split() == ["1", "message", "Why", "does", "it", "drop", "the", "object?"]
    assert paused.output.split() == ["2", "control", "pause"]
    assert read.exit_code == 0, read.output
    assert [line.split()[:2] for line in read.output.splitlines()] == [
        ["1", "message"],
        ["2", "control"],
    ]


def test_a_control_the_loop_does_not_take_is_a_usage_error(stack: Stack) -> None:
    agent_session_id = started(stack)
    refused = stack.acme("agent-session", "control", agent_session_id, "approve")
    assert refused.exit_code == main.EXIT_USAGE, refused.output


def test_following_a_loop_that_succeeded_reads_to_its_end_and_exits_zero(stack: Stack) -> None:
    agent_session_id = started(stack)
    stack.acme("agent-session", "say", agent_session_id, "Investigate the drop.")
    a_loop_ran(stack, agent_session_id, LoopOutcome.SUCCEEDED)

    followed = stack.acme("agent-session", "steps", agent_session_id, "--follow")

    assert followed.exit_code == 0, followed.output
    lines = followed.output.splitlines()
    assert [line.split()[1] for line in lines] == [
        "message",
        "model_request",
        "model_response",
        "loop_ended",
    ]
    assert lines[-1].split()[2] == "succeeded"


def test_following_a_loop_that_parked_exits_by_it(stack: Stack) -> None:
    agent_session_id = started(stack)
    stack.acme("agent-session", "say", agent_session_id, "Push the fix.")
    a_loop_ran(stack, agent_session_id, None)

    followed = stack.acme("agent-session", "steps", agent_session_id, "--follow", "--after", "3")

    assert followed.exit_code == main.EXIT_UNSUCCESSFUL, followed.output
    assert followed.output.split() == ["4", "parked", "person,", "until", "approval"]


def test_a_loops_end_and_its_park_say_their_cause_on_their_line(stack: Stack) -> None:
    ended, parked = started(stack), started(stack)
    for agent_session_id in (ended, parked):
        stack.acme("agent-session", "say", agent_session_id, "Run the tests.")
    failed = "the base's setup command 1, 'pip install acme-tools', exited 2"
    a_loop_ran(stack, ended, LoopOutcome.ERRORED, cause=failed)
    lost = Park(reason=ParkReason.PERSON, unlock="agent_workspace", cause="the snapshot is gone")
    a_loop_ran(stack, parked, None, park=lost)

    end = stack.acme("agent-session", "steps", ended, "--after", "3")
    park = stack.acme("agent-session", "steps", parked, "--after", "3")

    assert end.output.strip().split(maxsplit=2) == ["4", "loop_ended", f"errored: {failed}"]
    assert park.output.strip().split(maxsplit=2) == [
        "4",
        "parked",
        "person, until agent_workspace: the snapshot is gone",
    ]


def a_call_failed_and_the_engine_nudged(stack: Stack, agent_session_id: str, said: str) -> None:
    """A run's steps: its response called a tool, policy denied the call
    with `said`, and the engine nudged the model to go on."""
    ctx = owner_context(stack)
    sid = UUID(agent_session_id)
    managers = stack.container.managers

    async def write() -> None:
        epoch = await managers.agent_steps.begin_run(ctx, sid)
        (message,) = (await managers.agent_steps.get_agent_steps(ctx, sid, 0, 10)).items
        loop_id = message.loop_id
        request = make_request(sid, loop_id, (message.id,))
        answer = make_response(sid, loop_id, request.id)
        call = make_tool_request(sid, loop_id, answer.id)
        failed = response(new_id(), utcnow(), call, said, ToolFailure.DENIED, limit=1_000)
        nudge = NUDGE.format(tool="submit_result")
        notice = notice_step(new_id(), utcnow(), sid, loop_id, a_person(), nudge)
        steps = [request, answer, call, failed, notice]
        await managers.agent_steps.append_agent_steps(ctx, sid, epoch, steps)

    run(write())


def test_a_person_reads_the_engines_notice_as_the_engines_and_a_failure_without_advice(
    stack: Stack,
) -> None:
    agent_session_id = started(stack)
    stack.acme("agent-session", "say", agent_session_id, "Clean the build root.")
    said = "the root is out of bounds"
    a_call_failed_and_the_engine_nudged(stack, agent_session_id, said)

    lines = stack.acme("agent-session", "steps", agent_session_id, "--after", "4").output
    views = stack.acme("agent-session", "steps", agent_session_id, "--after", "4", "--json")

    failed, notice = (line.strip().split(maxsplit=2) for line in lines.splitlines())
    assert failed == ["5", "tool_response", f"denied: {said}"]
    assert notice[:2] == ["6", "message"]
    assert notice[2].startswith("from the engine: Your turn ended without a tool call.")
    failure, nudged = (json.loads(line) for line in views.output.splitlines())
    assert (failure["failure"], failure["text"]) == ("denied", said)
    assert (nudged["actor"], nudged["origin"]) == ("engine", "engine")
    assert ADVICE[ToolFailure.DENIED] not in lines + views.output
