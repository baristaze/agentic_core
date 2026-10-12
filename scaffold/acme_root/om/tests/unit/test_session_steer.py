"""One steerer at a time. A participant who does not steer a session
speaks in it as data: the model reads their words quoted and labelled with
their author, never among its instructions, and their message neither
wakes the session nor pays for what the model reads. The steerer's words
render as themselves, an instruction, and the steerer pays."""

from pathlib import Path

from contracts.loops import loop_over, reply, said

from acme.om.agent_sessions.types.agent_session import SessionStatus
from acme.om.agents.types.run import RunEnd
from acme.om.attribution.rules import instructs, trust_of
from acme.om.attribution.types.authority import Trust
from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.steps.types.content import TextBlock
from acme.om.steps.types.header import InputHeader, LoopOutcome, ModelRequestHeader
from acme.om.steps.types.step import StepType
from acme.om.windows.rules import data_block

SUGGESTION = "Don't touch the retry settings."
ASK = "Find out why an order drops before its payment confirms."


async def test_a_participants_message_is_data_labelled_with_its_author_and_never_pays(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    owner, teammate = loop.owner, loop.colleague()
    sessions = loop.managers.agent_sessions
    await sessions.add_participant(owner, session_id, teammate.user_id)
    suggested = await loop.say(session_id, SUGGESTION, teammate)
    idle = await sessions.get_session(owner, session_id)
    await loop.say(session_id, ASK)
    loop.anthropic.add(reply(said("It drops when the payment times out.")))

    run = await loop.loops.run(owner, session_id)

    assert (run.end, run.outcome) == (RunEnd.ENDED, LoopOutcome.SUCCEEDED)
    assert idle.status is SessionStatus.IDLE, "a participant's message wakes nothing"
    header = suggested.header
    assert isinstance(header, InputHeader) and header.participant and header.waking is False
    assert header.principal.id == teammate.user_id
    assert trust_of(suggested) is Trust.DATA and not instructs(suggested)
    (call,) = loop.anthropic.calls
    quoted = data_block(
        "message",
        SUGGESTION,
        seq=suggested.seq,
        actor="person",
        via=suggested.origin.value,
        author=teammate.user_id,
    )
    turns = [message.blocks for message in call.messages if message.role == "user"]
    assert turns[0] == (quoted, TextBlock(text=ASK)), "the steerer's words, an instruction"
    assert all(SUGGESTION not in block.text for block in call.system), "never an instruction"
    (request,) = [s for s in await loop.history(session_id) if s.type is StepType.MODEL_REQUEST]
    assert isinstance(request.header, ModelRequestHeader)
    steerer = Principal(kind=PrincipalKind.PERSON, id=owner.user_id)
    assert (request.header.speaker, request.header.spender) == (steerer, steerer)
