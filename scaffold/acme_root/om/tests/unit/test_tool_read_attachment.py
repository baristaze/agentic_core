"""`read_attachment` through the loop: it answers the asked range of lines or
pages within its bound, refuses a range wider than the bound and one past
the file's end, cuts a range at the bound of one read, measured as the
model reads it, so a full read is kept whole, reads on within a line or
page longer than one read from the offset the answer before gave, refuses
an offset that is negative, at or past its line's end, or given with a
range wider than one line, and never reads an attachment another session
or another tenant holds: such an id answers as one that never existed, and
the reader is never asked."""

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from contracts.loops import ASSISTANT, DELIVERY, HELPER, Loop, call, loop_over, reply, said

from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.base import new_id, utcnow
from acme.om.context import TenantContext
from acme.om.steps.types.content import (
    Attachment,
    Children,
    Content,
    DocumentBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from acme.om.steps.types.header import InputHeader, LoopOutcome, ToolFailure, ToolResponseHeader
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.tools.attachments import AttachmentReaderInterface, AttachmentText

LINES = "\n".join(f"line {n}" for n in range(1, 451)) + "\n"
QUOTED = "".join(f'"{n}",' + ",".join(['"a ""quoted"" cell"'] * 6) + "\n" for n in range(1, 401))


class Reader(AttachmentReaderInterface):
    """The adopter's reader, twinned: the text of each file it holds, and
    every read it was asked for."""

    def __init__(self, texts: dict[UUID, AttachmentText]) -> None:
        self._texts = texts
        self.asked: list[tuple[UUID, UUID]] = []

    async def read_text(
        self, ctx: TenantContext, session_id: UUID, attachment: Attachment
    ) -> AttachmentText | None:
        self.asked.append((session_id, attachment.id))
        return self._texts.get(attachment.id)


def a_file(name: str = "weekly.txt") -> Attachment:
    return Attachment(id=new_id(), name=name, media_type="text/plain", size=9, hash="k:1")


async def attach(loop: Loop, session_id: UUID, attachment: Attachment) -> Step:
    """A person's message that carries a file."""
    step_id = new_id()
    message = Step(
        id=step_id,
        created_at=utcnow(),
        session_id=session_id,
        loop_id=step_id,
        type=StepType.MESSAGE,
        actor=Actor.PERSON,
        origin=Origin.PORTAL,
        header=InputHeader(principal=Principal(kind=PrincipalKind.PERSON, id=loop.owner.user_id)),
        content=Content(
            blocks=(TextBlock(text="Read the file."), DocumentBlock(attachment_id=attachment.id))
        ),
        children=Children(attachments=(attachment,)),
    )
    (stored,) = await loop.managers.steps.append_inputs(loop.owner, session_id, [message])
    return stored


def read(
    attachment_id: UUID, unit: str, first: int, last: int, offset: int | None = None
) -> ToolUseBlock:
    within = {} if offset is None else {"offset": offset}
    return call(
        "read_attachment",
        attachment_id=str(attachment_id),
        unit=unit,
        first=first,
        last=last,
        **within,
    )


def answers(steps: list[Step]) -> list[tuple[ToolFailure | None, str]]:
    found: list[tuple[ToolFailure | None, str]] = []
    for step in steps:
        if isinstance(step.header, ToolResponseHeader):
            texts = [p.text for p in step.as_tool_response().parts if isinstance(p, TextBlock)]
            found.append((step.header.failure, "\n".join(texts)))
    return found


def labels(loop: Loop) -> str:
    first = loop.anthropic.calls[0]
    return "\n".join(
        block.text
        for message in first.messages
        for block in message.blocks
        if isinstance(block, TextBlock) and not isinstance(block, ToolResultBlock)
    )


async def test_a_read_answers_its_range_within_the_bound_and_refuses_one_past_it(
    tmp_path: Path,
) -> None:
    report, long = a_file(), a_file("long.txt")
    reader = Reader(
        {
            report.id: AttachmentText(pages=("line 1\nline 2\nline 3\n", "line 4\n")),
            long.id: AttachmentText(pages=(LINES.replace("line", "x" * 60),)),
        }
    )
    loop = loop_over(tmp_path, kinds=(ASSISTANT, DELIVERY, HELPER), reader=reader)
    session_id = await loop.start("helper")
    await attach(loop, session_id, report)
    await attach(loop, session_id, long)
    loop.anthropic.add(
        reply(read(report.id, "lines", 2, 9)),
        reply(read(report.id, "pages", 2, 2)),
        reply(read(report.id, "lines", 1, 401)),
        reply(read(report.id, "pages", 3, 3)),
        reply(read(long.id, "lines", 1, 400)),
        reply(said("I read it.")),
    )

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    assert f'attachment="{report.id}"' in labels(loop), "the window names the file's id"
    lines, page, wide, past, cut = answers(await loop.history(session_id))
    assert lines[0] is None and json.loads(lines[1]) == {
        "attachment_id": str(report.id),
        "name": "weekly.txt",
        "unit": "lines",
        "first": 2,
        "last": 4,
        "total": 4,
        "text": "line 2\nline 3\nline 4",
        "cut": False,
    }
    assert page[0] is None and json.loads(page[1])["text"] == "[page 2]\nline 4\n"
    assert wide[0] is ToolFailure.INVALID_INPUT and "at most 400 lines" in wide[1]
    assert past[0] is ToolFailure.INVALID_INPUT and "has 2 pages" in past[1]
    held = json.loads(cut[1])
    assert cut[0] is None and held["cut"] is True and held["total"] == 450
    assert held["last"] < 400 and len(held["text"]) <= 20_000
    assert held["text"].split("\n")[-1] == "x" * 60 + f" {held['last']}", "whole lines only"
    # A range wider than the bound is refused before the file is read.
    assert reader.asked == [(session_id, report.id)] * 3 + [(session_id, long.id)]


async def test_a_quoted_range_that_fills_the_bound_is_answered_whole_never_as_a_preview(
    tmp_path: Path,
) -> None:
    """Every quote doubles in the answer's JSON, so the bound is measured
    there: past it, the window would keep the read as an artifact and show
    only its head and tail."""
    sheet = a_file("weekly.csv")
    reader = Reader({sheet.id: AttachmentText(pages=(QUOTED,))})
    loop = loop_over(tmp_path, kinds=(ASSISTANT, DELIVERY, HELPER), reader=reader)
    session_id = await loop.start("helper")
    await attach(loop, session_id, sheet)
    loop.anthropic.add(reply(read(sheet.id, "lines", 1, 400)), reply(said("I read it.")))

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    (response,) = [s for s in await loop.history(session_id) if s.type is StepType.TOOL_RESPONSE]
    assert isinstance(response.header, ToolResponseHeader) and response.header.artifact is None
    ((failure, text),) = answers([response])
    assert failure is None and len(text) <= 20_000, "within the bound as the model reads it"
    held = json.loads(text)
    assert held["cut"] is True and held["first"] == 1 and held["last"] < 400
    assert held["text"].split("\n") == QUOTED.split("\n")[: held["last"]], "whole lines only"


async def test_a_line_longer_than_one_read_is_read_whole_from_the_offset_each_answer_gives(
    tmp_path: Path,
) -> None:
    """A one-line export: past one read, an answer stops within the line and
    gives the offset the next read starts at, until the line ends."""
    export = a_file("export.csv")
    line = "".join(f"{n:09d}," for n in range(6_000))
    assert len(line) == 60_000
    reader = Reader({export.id: AttachmentText(pages=(line + "\n",))})
    loop = loop_over(tmp_path, kinds=(ASSISTANT, DELIVERY, HELPER), reader=reader)
    session_id = await loop.start("helper")
    await attach(loop, session_id, export)
    held: list[dict[str, Any]] = []
    at: int | None = None

    for n in range(4):
        asked = read(export.id, "lines", 1, 400) if n == 0 else read(export.id, "lines", 1, 1, at)
        loop.anthropic.add(reply(asked), reply(said("Read on.")))
        if n:
            await loop.say(session_id, "Read on.")
        run = await loop.loops.run(loop.owner, session_id)
        assert run.outcome is LoopOutcome.SUCCEEDED
        failure, text = answers(await loop.history(session_id))[-1]
        assert failure is None and len(text) <= 20_000, "each read within the bound"
        held.append(json.loads(text))
        at = held[-1].get("offset")

    # One read holds under 20,000 of its characters, so 60,000 take four.
    assert [h["cut"] for h in held] == [True, True, True, False]
    assert ["offset" in h for h in held] == [True, True, True, False], "an offset only within"
    assert all(h["first"] == h["last"] == h["total"] == 1 for h in held)
    assert "".join(h["text"] for h in held) == line, "read whole, nothing twice"


async def test_an_offset_negative_past_its_line_or_with_a_wider_range_is_refused(
    tmp_path: Path,
) -> None:
    report = a_file()
    reader = Reader({report.id: AttachmentText(pages=("line 1\nline 2\nline 3\n", "line 4\n"))})
    loop = loop_over(tmp_path, kinds=(ASSISTANT, DELIVERY, HELPER), reader=reader)
    session_id = await loop.start("helper")
    await attach(loop, session_id, report)
    loop.anthropic.add(
        reply(read(report.id, "lines", 2, 2, 6)),
        reply(read(report.id, "pages", 2, 2, 99)),
        reply(read(report.id, "lines", 1, 1, -1)),
        reply(read(report.id, "lines", 1, 3, 2)),
        reply(read(report.id, "lines", 2, 2, 5)),
        reply(said("I read it.")),
    )

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    at_end, past_page, negative, wider, within = answers(await loop.history(session_id))
    assert at_end[0] is ToolFailure.INVALID_INPUT
    assert "line 2 of weekly.txt has 6 characters; offset 6 is at or past its end" in at_end[1]
    assert past_page[0] is ToolFailure.INVALID_INPUT and "offset 99 is at or past" in past_page[1]
    assert negative[0] is ToolFailure.INVALID_INPUT and "offset: Input should be" in negative[1]
    assert wider[0] is ToolFailure.INVALID_INPUT and "an offset reads within one" in wider[1]
    assert within[0] is None and json.loads(within[1])["text"] == "2", "a valid offset reads"
    # A negative offset, and one beside a wider range, are refused before the file is read.
    assert reader.asked == [(session_id, report.id)] * 3


async def test_a_read_never_reaches_another_sessions_or_another_tenants_attachment(
    tmp_path: Path,
) -> None:
    report = a_file()
    reader = Reader({report.id: AttachmentText(pages=("the total is 12\n",))})
    loop = loop_over(tmp_path / "one", kinds=(ASSISTANT, DELIVERY, HELPER), reader=reader)
    holder = await loop.start("helper")
    await attach(loop, holder, report)
    other = await loop.start("helper")
    await loop.say(other, "Read the weekly report.")
    stranger = loop_over(
        tmp_path / "two", kinds=(ASSISTANT, DELIVERY, HELPER), storage=loop.storage, reader=reader
    )
    theirs = await stranger.start("helper")
    await stranger.say(theirs, "Read the weekly report.")
    for each in (loop, stranger):
        each.anthropic.add(reply(read(report.id, "lines", 1, 1)), reply(said("I cannot.")))

    for each, session_id in ((loop, other), (stranger, theirs)):
        run = await each.loops.run(each.owner, session_id)

        assert run.outcome is LoopOutcome.SUCCEEDED
        ((failure, text),) = answers(await each.history(session_id))
        assert failure is ToolFailure.INVALID_INPUT
        assert f"this session holds no attachment {report.id}" in text
    assert reader.asked == [], "the reader is never asked for a file the session lacks"
