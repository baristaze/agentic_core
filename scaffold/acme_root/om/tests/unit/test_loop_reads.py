"""What a run reads of its history: the whole of it once, at its start, and
from then on only what others wrote. What the run appends joins the
history it holds, and is never read back, also when another writer's step
falls between two of the run's."""

import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from acme.om.agent_steps.storage import AgentStepStorageInterface
from acme.om.agent_steps.types.agent_step import AgentStep
from acme.om.agent_steps.types.header import LoopOutcome
from acme.om.agent_steps.types.page import AgentStepCursor
from acme.om.agents.types.run import RunEnd
from acme.om.storage.impl.memory import StorageMemoryImpl

from contracts.loops import loop_over, reply, said, use

HISTORY = "/acme/om/agents/impl/loop_core.py"


@dataclass
class Read:
    after: int
    seqs: list[int]


@dataclass
class LoopReads(AgentStepStorageInterface):
    """The history, with every read the loop's own history read makes kept:
    where it began and the steps it found."""

    inner: AgentStepStorageInterface
    reads: list[Read] = field(default_factory=lambda: [])

    async def begin_run(self, org_id: UUID, agent_session_id: UUID) -> int:
        return await self.inner.begin_run(org_id, agent_session_id)

    async def append_agent_steps(
        self, org_id: UUID, agent_session_id: UUID, epoch: int, steps: Sequence[AgentStep]
    ) -> tuple[AgentStep, ...]:
        return await self.inner.append_agent_steps(org_id, agent_session_id, epoch, steps)

    async def append_inputs(
        self, org_id: UUID, agent_session_id: UUID, steps: Sequence[AgentStep]
    ) -> tuple[AgentStep, ...]:
        return await self.inner.append_inputs(org_id, agent_session_id, steps)

    async def read_agent_steps(
        self, org_id: UUID, agent_session_id: UUID, after_seq: int, limit: int
    ) -> list[AgentStep]:
        found = await self.inner.read_agent_steps(org_id, agent_session_id, after_seq, limit)
        frame = sys._getframe(1)
        while frame is not None:
            if frame.f_code.co_filename.endswith(HISTORY) and frame.f_code.co_name == "_history":
                self.reads.append(Read(after_seq, [step.seq for step in found]))
                break
            frame = frame.f_back  # type: ignore[assignment]
        return found

    async def read_cursor(self, org_id: UUID, agent_session_id: UUID) -> AgentStepCursor:
        return await self.inner.read_cursor(org_id, agent_session_id)

    async def purge_history(self, org_id: UUID, agent_session_id: UUID, limit: int) -> int:
        return await self.inner.purge_history(org_id, agent_session_id, limit)

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        return await self.inner.purge_tenant(org_id, limit)


async def test_a_run_reads_its_history_whole_once_and_reads_back_nothing_it_wrote(
    tmp_path: Path,
) -> None:
    storage = StorageMemoryImpl()
    counted = LoopReads(storage.get_agent_step_storage())
    storage.get_agent_step_storage = lambda: counted  # type: ignore[method-assign]
    loop = loop_over(tmp_path, storage=storage)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Note the fix, then say so.")
    loop.anthropic.add(
        reply(use("note", use_id="use_1")),
        reply(use("note", use_id="use_2")),
        reply(said("Noted twice.")),
    )
    before = len(await loop.history(agent_session_id))
    counted.reads.clear()

    run = await loop.loops.run(loop.owner, agent_session_id)

    assert (run.end, run.outcome) == (RunEnd.ENDED, LoopOutcome.SUCCEEDED)
    written = len(await loop.history(agent_session_id)) - before
    assert written >= 6, "two calls and their answers, and the turns around them"
    assert [read.after for read in counted.reads if read.after == 0] == [0], (
        "the history is read whole once, at the run's start"
    )
    read_back = [seq for read in counted.reads for seq in read.seqs if seq > before]
    assert read_back == [], "a step the run appended joins its history unread"


async def test_a_step_another_writes_mid_run_is_read_and_the_runs_own_are_not(
    tmp_path: Path,
) -> None:
    storage = StorageMemoryImpl()
    counted = LoopReads(storage.get_agent_step_storage())
    storage.get_agent_step_storage = lambda: counted  # type: ignore[method-assign]
    loop = loop_over(tmp_path, storage=storage)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Look it up.")
    loop.anthropic.add(
        reply(use("slow", use_id="use_slow")),
        reply(use("note", use_id="use_note")),
        reply(said("Looked up, and noted.")),
    )
    before = len(await loop.history(agent_session_id))
    counted.reads.clear()
    slow = loop.tools["slow"]

    running = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await slow.started.wait()
    theirs = await loop.say(agent_session_id, "Note it too.")
    slow.release.set()
    run = await running

    assert (run.end, run.outcome) == (RunEnd.ENDED, LoopOutcome.SUCCEEDED)
    read = [seq for r in counted.reads for seq in r.seqs if seq > before]
    # A page reads one step past what it answers, to tell whether more follow.
    assert read in ([theirs.seq], [theirs.seq, theirs.seq + 1]), (
        "the other writer's step, and of the run's none but the one past it"
    )
    seen = [m for call in loop.anthropic.calls[1:] for m in call.messages]
    assert any("Note it too." in str(m) for m in seen), "the model read it"
