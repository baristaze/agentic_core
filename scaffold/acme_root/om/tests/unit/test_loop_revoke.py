"""A revocation reaches a run under way: the history the run holds opened
lets nothing past it. The run's next append of content is refused, no
model call goes out after the revocation, and the next read of the
session reads what it said as absent."""

import asyncio
from pathlib import Path

import pytest

from acme.om.agent_steps.types.content import ContentState
from acme.om.exceptions import KeyRevoked

from contracts.loops import loop_over, reply, said, use


async def test_a_run_under_way_when_its_key_is_revoked_sends_and_keeps_nothing_after(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Look it up, then say what you found.")
    loop.anthropic.add(
        reply(use("slow", use_id="use_slow")),
        reply(said("Found it.")),
    )
    slow = loop.tools["slow"]

    running = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await slow.started.wait()
    await loop.managers.privacy.revoke_key(loop.owner, agent_session_id)
    calls = len(loop.anthropic.calls)
    held = len(await loop.history(agent_session_id))
    slow.release.set()

    with pytest.raises(KeyRevoked):
        await running

    assert len(loop.anthropic.calls) == calls, "no model call after the revocation"
    after = await loop.history(agent_session_id)
    assert len(after) == held, "the tool's answer was refused"
    said_steps = [step for step in after if step.content.state is not ContentState.PLAIN]
    assert said_steps, "what the session said reads as absent"
    assert all(step.content.state is ContentState.ABSENT for step in said_steps)
    assert not [step for step in after if step.content.blocks]
