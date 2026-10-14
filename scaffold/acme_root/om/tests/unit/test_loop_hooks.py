"""A layer above the engine extends a run through the loop's hooks alone,
with no edit to the loop: the toolsets it attaches to a session, held and
deferred, and what it does before a run's first call, a renewal it runs
between loops and a step that tells the model what changed under it. The
layer here is such a layer's hooks, as it would write them."""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

import pytest
from contracts.loops import ASSISTANT, Lookup, control, loop_over, reply, said, text_of, use

from acme.infra.agent_workspaces import AgentWorkspace, IsolationRefused
from acme.integrations.model_providers.calls import ModelCall
from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.agent_steps.types.agent_step import Actor, AgentStep, AgentStepType, Origin
from acme.om.agent_steps.types.content import Content, TextBlock, ToolResultBlock, ToolUseBlock
from acme.om.agent_steps.types.header import (
    ControlCommand,
    LoopOutcome,
    MarkHeader,
    ParkReason,
)
from acme.om.agents.hooks import (
    NO_ATTACHED_TOOLSETS,
    AttachedToolsets,
    LoopHooksInterface,
    LoopStart,
    with_attached_toolsets,
)
from acme.om.agents.types.run import RunEnd
from acme.om.base import new_id, utcnow
from acme.om.context import TenantContext
from acme.om.exceptions import UnresolvedRole
from acme.om.tools.types.toolset import LOAD_TOOLSET, Toolset

LEDGER = Toolset(name="ledger", version=1, summary="The tenant's ledger.", tools=("ledger_read",))
ARCHIVE = Toolset(
    name="archive", version=1, summary="The tenant's archive.", tools=("archive_search",)
)
CHANGED = "the files under the workspace were rebuilt since your last loop"


class Layer(LoopHooksInterface):
    """A layer's hooks: what it attaches to every session, a renewal it runs
    at a new loop under the run's epoch, and a step it appends before each
    run's first call when something changed. `refuses` stands for a renewal
    that finds no fill for the session."""

    def __init__(
        self,
        attached: AttachedToolsets = NO_ATTACHED_TOOLSETS,
        *,
        changed: str | None = None,
        refuses: bool = False,
    ) -> None:
        self.attached = attached
        self.changed = changed
        self.refuses = refuses
        self.asked: list[UUID] = []
        self.starts: list[LoopStart] = []
        self.renewed: list[tuple[UUID, int]] = []

    async def attached_toolsets(
        self, ctx: TenantContext, agent_session: AgentSession
    ) -> AttachedToolsets:
        self.asked.append(agent_session.id)
        return self.attached

    async def before_first_call(self, ctx: TenantContext, start: LoopStart) -> Sequence[AgentStep]:
        self.starts.append(start)
        if start.new_loop:
            if self.refuses:
                raise UnresolvedRole("no fill serves the main role any longer")
            self.renewed.append((start.loop_id, start.epoch))
        if self.changed is None:
            return ()
        changed = AgentStep(
            id=new_id(),
            created_at=utcnow(),
            agent_session_id=start.agent_session.id,
            loop_id=start.loop_id,
            type=AgentStepType.ENVIRONMENT_CHANGED,
            actor=Actor.ENGINE,
            origin=Origin.ENGINE,
            header=MarkHeader(),
            content=Content(blocks=(TextBlock(text=self.changed),)),
        )
        return (changed,)


def offered(call: ModelCall) -> list[str]:
    return sorted(tool.name for tool in call.tools)


def answers(steps: list[AgentStep]) -> list[str]:
    texts: list[str] = []
    for step in steps:
        if step.type is not AgentStepType.TOOL_RESPONSE or step.content is None:
            continue
        for block in step.content.blocks:
            parts = block.parts if isinstance(block, ToolResultBlock) else (block,)
            texts.extend(part.text for part in parts if isinstance(part, TextBlock))
    return texts


async def test_a_session_calls_its_attached_toolsets_held_from_the_start_and_loaded(
    tmp_path: Path,
) -> None:
    ledger, archive = Lookup("ledger_read"), Lookup("archive_search")
    layer = Layer(AttachedToolsets(held=(LEDGER,), deferred=(ARCHIVE,), tools=(ledger, archive)))
    loop = loop_over(tmp_path, hooks=layer)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "What do the ledger and the archive say?")
    load = ToolUseBlock(id="use_load", name=LOAD_TOOLSET, input={"toolset": "archive"})
    loop.anthropic.add(
        reply(use("ledger_read")),
        reply(load),
        reply(use("archive_search")),
        reply(said("Both say 12.")),
    )

    ran = await loop.loops.run(loop.owner, agent_session_id)

    assert ran.outcome is LoopOutcome.SUCCEEDED
    first, loaded, searched, _ = loop.anthropic.calls
    assert {"ledger_read", LOAD_TOOLSET} <= set(offered(first)), "held from the start"
    assert "archive_search" not in offered(first), "a deferred toolset waits for its load"
    assert "archive_search" not in offered(loaded), "never for a call of the turn that loaded it"
    assert "archive_search" in offered(searched), "the next request offers what the load added"
    assert ledger.ran_as == [loop.owner.user_id] and archive.ran_as == [loop.owner.user_id]
    steps = await loop.history(agent_session_id)
    assert '{"text":"ledger_read found the total"}' in answers(steps)
    assert '{"text":"archive_search found the total"}' in answers(steps)
    assert [s.type for s in steps].count(AgentStepType.SWITCHED) == 1, "the load is recorded"
    assert layer.asked == [agent_session_id], "asked once a run"


async def test_a_step_the_hooks_append_reaches_the_model_before_the_runs_first_call(
    tmp_path: Path,
) -> None:
    layer = Layer(changed=CHANGED)
    loop = loop_over(tmp_path, hooks=layer)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(said("It is 12.")))

    ran = await loop.loops.run(loop.owner, agent_session_id)

    assert ran.outcome is LoopOutcome.SUCCEEDED
    types = [step.type for step in await loop.history(agent_session_id)]
    changed = types.index(AgentStepType.ENVIRONMENT_CHANGED)
    assert changed < types.index(AgentStepType.MODEL_REQUEST), "written before the first call"
    assert CHANGED in text_of(loop.anthropic.calls[0]), "and the first request carries it"
    (start,) = layer.starts
    assert start.new_loop and start.agent_session.id == agent_session_id
    assert layer.renewed == [(ran.loop_id, ran.epoch)], "renewed under the run's own epoch"


async def test_the_hooks_tell_a_new_loop_from_one_a_run_takes_up_again(tmp_path: Path) -> None:
    layer = Layer()
    loop = loop_over(tmp_path, hooks=layer)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(use("slow", use_id="use_slow")), reply(said("It is 12.")))
    slow = loop.tools["slow"]
    running = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await slow.started.wait()
    await loop.managers.agent_steps.append_inputs(
        loop.owner, agent_session_id, [control(loop, agent_session_id, ControlCommand.PAUSE)]
    )
    slow.release.set()
    paused = await running
    assert paused.park is not None and paused.park.reason is ParkReason.PAUSE
    await loop.managers.agent_steps.append_inputs(
        loop.owner, agent_session_id, [control(loop, agent_session_id, ControlCommand.RESUME)]
    )

    resumed = await loop.loops.run(loop.owner, agent_session_id)

    assert resumed.outcome is LoopOutcome.SUCCEEDED
    assert [start.new_loop for start in layer.starts] == [True, False]
    assert [start.epoch for start in layer.starts] == [paused.epoch, resumed.epoch]
    assert layer.renewed == [(paused.loop_id, paused.epoch)], "once, at the new loop"


async def test_a_loop_whose_first_run_parks_before_its_first_call_is_new_to_the_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer = Layer()
    loop = loop_over(tmp_path, hooks=layer)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(said("It is 12.")))
    prepare = loop.managers.tools.prepare_agent_workspace

    async def refused(*args: object, **kwargs: object) -> AgentWorkspace:
        raise IsolationRefused("no host can give it the workspace yet", clears=True)

    monkeypatch.setattr(loop.managers.tools, "prepare_agent_workspace", refused)
    waiting = await loop.loops.run(loop.owner, agent_session_id)
    monkeypatch.setattr(loop.managers.tools, "prepare_agent_workspace", prepare)
    await loop.managers.agent_steps.append_inputs(
        loop.owner, agent_session_id, [control(loop, agent_session_id, ControlCommand.UNLOCK)]
    )

    done = await loop.loops.run(loop.owner, agent_session_id)

    assert waiting.park is not None and waiting.park.reason is ParkReason.RESOURCE
    assert done.outcome is LoopOutcome.SUCCEEDED and done.loop_id == waiting.loop_id
    assert [start.new_loop for start in layer.starts] == [True], "new until its first call"
    assert layer.renewed == [(done.loop_id, done.epoch)], "renewed before that call"


async def test_a_renewal_that_finds_no_fill_ends_the_loop_before_any_call(tmp_path: Path) -> None:
    loop = loop_over(tmp_path, hooks=Layer(refuses=True))
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(said("It is 12.")))

    ran = await loop.loops.run(loop.owner, agent_session_id)

    assert ran.end is RunEnd.ENDED and ran.outcome is LoopOutcome.ERRORED
    assert loop.anthropic.calls == [], "nothing is spent on a loop with no fill"


def test_attached_toolsets_fold_into_the_kind_and_give_each_tool_once() -> None:
    attached = AttachedToolsets(
        held=(LEDGER,), deferred=(ARCHIVE,), tools=(Lookup("ledger_read"), Lookup("archive_search"))
    )
    folded = with_attached_toolsets(ASSISTANT, attached)
    assert {"ledger_read", "archive_search", LOAD_TOOLSET} <= set(folded.tools)
    assert folded.waiting == frozenset({"archive_search"})
    assert with_attached_toolsets(folded, NO_ATTACHED_TOOLSETS) is folded
    assert attached.names == ("ledger_read", "archive_search", LOAD_TOOLSET)
    with pytest.raises(ValueError, match="given once"):
        AttachedToolsets(held=(LEDGER,), deferred=(), tools=())
