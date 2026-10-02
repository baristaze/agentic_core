"""A session from the terminal, against the whole API in-process: started,
spoken to, steered, and read one step a line; and `steps --follow`, which
reads until the loop stops and exits by how it ended. No runner works
behind the API here, so a case writes the loop's steps itself, the way a
run writes them."""

from uuid import UUID

from api_support import OWNER, run, seed_request
from cli_support import Stack
from contracts.step_storage import make_request, make_response

from acme.apps.cli import main
from acme.om.agents.loop_rules import ended_step
from acme.om.base import new_id, utcnow
from acme.om.context import TenantContext
from acme.om.steps.types.header import LoopOutcome, Park, ParkReason


def owner_context(stack: Stack) -> TenantContext:
    tenancy = stack.container.managers.tenancy
    return run(tenancy.authenticate(seed_request(), stack.session_token(OWNER["email"])))


def started(stack: Stack) -> str:
    result = stack.acme("session", "start", "assistant", "the dropped object")
    assert result.exit_code == 0, result.output
    assert result.output.startswith("started ") and result.output.endswith(" on assistant 1\n")
    return result.output.split()[1]


def a_loop_ran(stack: Stack, session_id: str, outcome: LoopOutcome | None) -> None:
    """A run's steps: its request delivered the message and was answered,
    then the loop ended with `outcome`, or parked for a person with None."""
    ctx = owner_context(stack)
    sid = UUID(session_id)
    managers = stack.container.managers

    async def write() -> None:
        epoch = await managers.steps.begin_run(ctx, sid)
        (message,) = (await managers.steps.get_steps(ctx, sid, 0, 10)).items
        request = make_request(sid, message.loop_id, (message.id,))
        response = make_response(sid, message.loop_id, request.id)
        await managers.steps.append_steps(ctx, sid, epoch, [request, response])
        if outcome is None:
            park = Park(reason=ParkReason.PERSON, unlock="approval")
            await managers.agent_sessions.park(ctx, sid, epoch, message.loop_id, park)
            return
        ended = ended_step(new_id(), utcnow(), sid, message.loop_id, outcome)
        await managers.steps.append_steps(ctx, sid, epoch, [ended])
        await managers.agent_sessions.project_status(ctx, sid)

    run(write())


def test_a_session_is_started_spoken_to_steered_and_read(stack: Stack) -> None:
    session_id = started(stack)

    said = stack.acme("session", "say", session_id, "Why does it drop the object?")
    paused = stack.acme("session", "control", session_id, "pause")
    read = stack.acme("session", "steps", session_id)

    assert said.exit_code == 0 and paused.exit_code == 0, said.output + paused.output
    assert said.output.split() == ["1", "message", "Why", "does", "it", "drop", "the", "object?"]
    assert paused.output.split() == ["2", "control", "pause"]
    assert read.exit_code == 0, read.output
    assert [line.split()[:2] for line in read.output.splitlines()] == [
        ["1", "message"],
        ["2", "control"],
    ]


def test_a_control_the_loop_does_not_take_is_a_usage_error(stack: Stack) -> None:
    session_id = started(stack)
    refused = stack.acme("session", "control", session_id, "approve")
    assert refused.exit_code == main.EXIT_USAGE, refused.output


def test_following_a_loop_that_succeeded_reads_to_its_end_and_exits_zero(stack: Stack) -> None:
    session_id = started(stack)
    stack.acme("session", "say", session_id, "Investigate the drop.")
    a_loop_ran(stack, session_id, LoopOutcome.SUCCEEDED)

    followed = stack.acme("session", "steps", session_id, "--follow")

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
    session_id = started(stack)
    stack.acme("session", "say", session_id, "Push the fix.")
    a_loop_ran(stack, session_id, None)

    followed = stack.acme("session", "steps", session_id, "--follow", "--after", "3")

    assert followed.exit_code == main.EXIT_UNSUCCESSFUL, followed.output
    assert followed.output.split() == ["4", "parked", "person,", "until", "approval"]
