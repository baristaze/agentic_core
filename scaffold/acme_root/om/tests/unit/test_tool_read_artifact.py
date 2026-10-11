"""`read_artifact` through the loop: a result above the size bound is kept
as an artifact, the notice between its head and its tail names the tool and
the handle, and a read by that handle, or by its step's, answers exactly
the kept text at the asked range, bounded as the model reads it so the
answer is kept in its step whole. A result a compaction elided reads back
whole by the handle its stub names. A handle another session or another
tenant holds answers as one that never existed, and an offset at or past
the end, a malformed handle, a negative offset, and a zero limit are each
refused with the error the model reads."""

import json
import re
from pathlib import Path
from uuid import UUID

from contracts.histories import History
from contracts.loops import (
    ALLOWED,
    ASSISTANT,
    DELIVERY,
    Found,
    Lookup,
    Loop,
    call,
    loop_over,
    reply,
    said,
    use,
)

from acme.om.agents.types.kind import AgentKind, DoneRule, TreeLimits
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.base import Platform
from acme.om.context import TenantContext
from acme.om.steps.types.content import TextBlock, ToolResultBlock, ToolUseBlock
from acme.om.steps.types.header import (
    ControlCommand,
    LoopOutcome,
    ToolFailure,
    ToolResponseHeader,
)
from acme.om.steps.types.step import Step, StepType
from acme.om.storage.root import StorageInterface
from acme.om.tools.tool import ToolRuntime
from acme.om.tools.types.tool import ToolInput

LARGE = "".join(f'{n:06d} "row"\n' for n in range(2_500))
"""A dump whose answer is past the size bound, with quotes and newlines the
answer's JSON escapes."""
MEDIUM = "".join(f"{n:05d} b\n" for n in range(800))
"""A dump within the size bound, past the length a compaction elides."""
HANDLE = re.compile(r"(?:artifact|step) ([0-9a-f-]{36})")

READER = AgentKind(
    name="reader",
    version=1,
    tools=("lookup", "dump", "bulky", "read_artifact"),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    tree=TreeLimits(height=1, count=0),
    prompts=("You read the records.",),
    policy=ALLOWED,
)
"""A kind that holds the read tool beside two tools with long answers."""


class Dump(Lookup):
    """A read-only tool whose answer is the same long text every call."""

    def __init__(self, name: str, text: str) -> None:
        super().__init__(name)
        self.text = text

    async def run(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> Platform:
        return Found(text=self.text)


def a_loop(tmp_path: Path, storage: StorageInterface | None = None) -> Loop:
    dumps = (Dump("dump", LARGE), Dump("bulky", MEDIUM))
    return loop_over(tmp_path, kinds=(ASSISTANT, DELIVERY, READER), storage=storage, extra=dumps)


def read(handle: UUID | str, **within: object) -> ToolUseBlock:
    return call("read_artifact", handle=str(handle), **within)


def responses(steps: list[Step]) -> list[Step]:
    return [step for step in steps if step.type is StepType.TOOL_RESPONSE]


def answer(step: Step) -> tuple[ToolFailure | None, str]:
    assert isinstance(step.header, ToolResponseHeader)
    texts = [p.text for p in step.as_tool_response().parts if isinstance(p, TextBlock)]
    return step.header.failure, "\n".join(texts)


def rendered_result(loop: Loop, tool_use_id: str) -> ToolResultBlock:
    """The result of `tool_use_id` as the latest request that carried it
    rendered it."""
    found = [
        block
        for sent in loop.anthropic.calls
        for message in sent.messages
        for block in message.blocks
        if isinstance(block, ToolResultBlock) and block.tool_use_id == tool_use_id
    ]
    return found[-1]


async def stored(loop: Loop, session_id: UUID, artifact_id: UUID) -> str:
    """The whole text the artifact keeps, read from the store a page at a
    time."""
    pages, offset = [], 0
    while True:
        page = await loop.managers.windows.get_artifact(
            loop.owner, session_id, artifact_id, offset, 100_000
        )
        pages.append(page.text)
        offset += len(page.text)
        if not page.has_more:
            return "".join(pages)


async def test_a_read_by_the_handle_a_notice_names_answers_the_kept_text_at_its_range(
    tmp_path: Path,
) -> None:
    loop = a_loop(tmp_path)
    session_id = await loop.start("reader")
    await loop.say(session_id, "Read the dump.")
    dumped = use("dump")
    loop.anthropic.add(reply(dumped), reply(said("It is long.")))
    assert (await loop.loops.run(loop.owner, session_id)).outcome is LoopOutcome.SUCCEEDED
    (kept,) = responses(await loop.history(session_id))
    assert isinstance(kept.header, ToolResponseHeader) and kept.header.artifact is not None
    whole = await stored(loop, session_id, kept.header.artifact.id)
    assert whole == Found(text=LARGE).model_dump_json(), "the artifact keeps the whole answer"
    _, notice, _ = rendered_result(loop, dumped.id).parts
    assert isinstance(notice, TextBlock) and "read_artifact pages through it" in notice.text
    (named,) = HANDLE.findall(notice.text)
    assert named == str(kept.header.artifact.id), "the notice names the artifact's handle"

    await loop.say(session_id, "Read part of it.")
    loop.anthropic.add(
        reply(read(named, offset=12_345, limit=678)),
        reply(read(kept.id, offset=12_345, limit=678)),
        reply(read(named, offset=10_000)),
        reply(said("I read it.")),
    )
    assert (await loop.loops.run(loop.owner, session_id)).outcome is LoopOutcome.SUCCEEDED

    by_artifact, by_step, rest = responses(await loop.history(session_id))[1:]
    failure, text = answer(by_artifact)
    assert failure is None and json.loads(text) == {
        "handle": named,
        "offset": 12_345,
        "text": whole[12_345:13_023],
        "characters": len(whole),
        "next_offset": 13_023,
    }
    failure, text = answer(by_step)
    assert failure is None and json.loads(text)["text"] == whole[12_345:13_023], (
        "a step's handle reads its artifact"
    )
    failure, text = answer(rest)
    page = json.loads(text)
    assert failure is None and len(text) <= 20_000, "one answer is within its bound"
    assert page["text"] == whole[10_000 : 10_000 + len(page["text"])]
    assert page["next_offset"] == 10_000 + len(page["text"]) < len(whole), "read on from there"
    assert isinstance(rest.header, ToolResponseHeader) and rest.header.artifact is None, (
        "a page is kept in its step whole, never as an artifact"
    )


async def test_a_result_a_compaction_elided_reads_back_whole_by_the_handle_its_stub_names(
    tmp_path: Path,
) -> None:
    loop = a_loop(tmp_path)
    session_id = await loop.start("reader")
    await loop.say(session_id, "Find the total. " + "The ledger is long. " * 2_500)
    bulky = use("bulky")
    loop.anthropic.add(
        reply(use("lookup")), reply(bulky), reply(use("lookup")), reply(said("Read."))
    )
    assert (await loop.loops.run(loop.owner, session_id)).outcome is LoopOutcome.SUCCEEDED
    elided = responses(await loop.history(session_id))[1]
    whole = Found(text=MEDIUM).model_dump_json()
    assert answer(elided) == (None, whole), "within the bound, kept in its step"

    control = History(session_id).control(ControlCommand.COMPACT)
    await loop.managers.steps.append_inputs(loop.owner, session_id, [control])
    await loop.say(session_id, "Read the bulky result again.")
    loop.anthropic.add(
        reply(said("Asked for the total; read the ledger."), model="claude-haiku-4-5"),
        reply(read(elided.id)),
        reply(said("I read it again.")),
    )
    assert (await loop.loops.run(loop.owner, session_id)).outcome is LoopOutcome.SUCCEEDED

    history = await loop.history(session_id)
    assert any(step.type is StepType.SUMMARY for step in history), "the window compacted"
    (stub,) = rendered_result(loop, bulky.id).parts
    assert isinstance(stub, TextBlock) and "elided once read" in stub.text
    assert "read_artifact reads it" in stub.text
    assert HANDLE.findall(stub.text) == [str(elided.id)], "the stub names the step's handle"
    failure, text = answer(responses(history)[-1])
    assert failure is None and json.loads(text) == {
        "handle": str(elided.id),
        "offset": 0,
        "text": whole,
        "characters": len(whole),
    }, "the whole result comes back, with nothing more to read"


async def test_a_read_never_reaches_another_session_and_refuses_what_it_cannot_read(
    tmp_path: Path,
) -> None:
    loop = a_loop(tmp_path / "one")
    holder = await loop.start("reader")
    await loop.say(holder, "Read both dumps.")
    loop.anthropic.add(reply(use("dump")), reply(use("bulky")), reply(said("Read.")))
    assert (await loop.loops.run(loop.owner, holder)).outcome is LoopOutcome.SUCCEEDED
    large, medium = responses(await loop.history(holder))
    assert isinstance(large.header, ToolResponseHeader) and large.header.artifact is not None
    artifact = large.header.artifact
    handles = (artifact.id, large.id, medium.id)

    other = await loop.start("reader")
    await loop.say(other, "Read the dump.")
    stranger = a_loop(tmp_path / "two", storage=loop.storage)
    theirs = await stranger.start("reader")
    await stranger.say(theirs, "Read the dump.")
    for each in (loop, stranger):
        each.anthropic.add(*(reply(read(handle)) for handle in handles), reply(said("None.")))
    for each, session_id in ((loop, other), (stranger, theirs)):
        assert (await each.loops.run(each.owner, session_id)).outcome is LoopOutcome.SUCCEEDED
        refused = [answer(step) for step in responses(await each.history(session_id))]
        assert [failure for failure, _ in refused] == [ToolFailure.INVALID_INPUT] * 3
        for (_, text), handle in zip(refused, handles, strict=True):
            assert f"this session keeps no result under {handle}" in text
            assert "000001" not in text and "00001 b" not in text, "nothing of the other session"

    # Two runs, so no run fails often enough in a row to end inconclusive.
    await loop.say(holder, "Read past the end.")
    loop.anthropic.add(
        reply(read(artifact.id, offset=artifact.characters)),
        reply(read(medium.id, offset=99_999)),
        reply(read("the dump")),
        reply(said("Done.")),
    )
    assert (await loop.loops.run(loop.owner, holder)).outcome is LoopOutcome.SUCCEEDED
    await loop.say(holder, "Read with a bad range.")
    loop.anthropic.add(
        reply(read(medium.id, offset=-1)), reply(read(medium.id, limit=0)), reply(said("Done."))
    )
    assert (await loop.loops.run(loop.owner, holder)).outcome is LoopOutcome.SUCCEEDED
    at_end, past, malformed, negative, empty = (
        answer(step) for step in responses(await loop.history(holder))[2:]
    )
    assert (
        at_end[0] is ToolFailure.INVALID_INPUT
        and (
            f"{artifact.id} holds {artifact.characters} characters; "
            f"offset {artifact.characters} is at or past its end"
        )
        in at_end[1]
    )
    assert past[0] is ToolFailure.INVALID_INPUT and "offset 99999 is at or past" in past[1]
    assert malformed[0] is ToolFailure.INVALID_INPUT and "handle" in malformed[1]
    assert negative[0] is ToolFailure.INVALID_INPUT and "offset" in negative[1]
    assert empty[0] is ToolFailure.INVALID_INPUT and "limit" in empty[1]
