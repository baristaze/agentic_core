"""`read_attachment`: a bounded read of one of the session's attachments,
by a range of lines or of pages, beside the window's rendering of the file
whole. The attachment is found in the history of the session the call was
made in (`ToolRuntime.session_id`), never one the input names, so an id
another session holds answers as one that never existed. Its text comes
from the adopter's reader (`tools.attachments`), and a range wider than the
bound is refused, so one read never floods the window."""

from collections.abc import Sequence
from datetime import timedelta
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from acme.om.base import Platform
from acme.om.context import TenantContext
from acme.om.exceptions import ToolFailed
from acme.om.steps import StepsManagerInterface
from acme.om.steps.types.content import Attachment
from acme.om.steps.types.header import ToolFailure
from acme.om.tools.attachments import AttachmentReaderInterface
from acme.om.tools.tool import ToolInterface, ToolRuntime
from acme.om.tools.types.policy import Target
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolSpec

READ_ATTACHMENT = "read_attachment"

Unit = Literal["lines", "pages"]

DESCRIPTION = (
    "Read part of a file attached to this session: a range of its lines, or "
    "of its pages, numbered from 1, both ends included. Name the file by the "
    "attachment id its label shows. Read a large file a range at a time; the "
    "answer says how many lines or pages the file has, and where it stopped."
)


class ReadAttachmentOptions(Platform):
    """The bound of one read. `max_chars` stays under the window's result
    bound, so an answer is kept in its step whole."""

    max_lines: int = Field(default=400, gt=0)
    max_pages: int = Field(default=10, gt=0)
    max_chars: int = Field(default=20_000, gt=0)
    page: int = Field(default=200, gt=0)  # steps one read of the history asks for


class ReadAttachmentInput(ToolInput):
    attachment_id: UUID
    unit: Unit = "lines"
    first: int = Field(ge=1)
    last: int = Field(ge=1)

    @model_validator(mode="after")
    def _a_range_runs_forward(self) -> Self:
        if self.last < self.first:
            raise ValueError("last is at or after first")
        return self


class AttachmentRange(Platform):
    """What one read answers: the lines or pages from `first` to `last`, of
    the `total` the file has. `cut` says the text stopped at the bound of
    one read before the range's end: read on from `last`."""

    attachment_id: UUID
    name: str
    unit: Unit
    first: int
    last: int
    total: int
    text: str
    cut: bool


class ReadAttachmentToolImpl(ToolInterface):
    def __init__(
        self,
        steps: StepsManagerInterface,
        reader: AttachmentReaderInterface,
        options: ReadAttachmentOptions | None = None,
    ) -> None:
        self._steps = steps
        self._reader = reader
        self._options = options or ReadAttachmentOptions()
        self._spec = ToolSpec(
            name=READ_ATTACHMENT,
            description=DESCRIPTION,
            input_model=ReadAttachmentInput,
            output_model=AttachmentRange,
            timeout=timedelta(minutes=1),
            authorization_class=ToolClass.READ,
            effect=Effect.READ_ONLY,
            interruptible=True,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def target(self, ctx: TenantContext, call_input: ToolInput) -> Target:
        return Target()

    async def preflight(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> None:
        assert isinstance(call_input, ReadAttachmentInput)
        bound = self._bound(call_input.unit)
        if call_input.last - call_input.first + 1 > bound:
            raise ToolFailed(
                ToolFailure.INVALID_INPUT,
                f"one read holds at most {bound} {call_input.unit}: ask for {call_input.first} "
                f"to {call_input.first + bound - 1}, then read on",
            )

    async def run(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> Platform:
        assert isinstance(call_input, ReadAttachmentInput)
        await self.preflight(ctx, call_input, runtime)
        attachment = await self._find(ctx, runtime.session_id, call_input.attachment_id)
        text = await self._reader.read_text(ctx, runtime.session_id, attachment)
        if text is None:
            raise ToolFailed(
                ToolFailure.PERMANENT,
                f"{attachment.name} ({attachment.media_type}) holds no text to read by range",
            )
        items = text.pages if call_input.unit == "pages" else _lines(text.pages)
        if call_input.first > len(items):
            raise ToolFailed(
                ToolFailure.INVALID_INPUT,
                f"{attachment.name} has {len(items)} {call_input.unit}; "
                f"{call_input.first} is past its end",
            )
        last = min(call_input.last, len(items))
        chosen, cut = _within(
            items[call_input.first - 1 : last],
            call_input.unit,
            call_input.first,
            self._options.max_chars,
        )
        return AttachmentRange(
            attachment_id=attachment.id,
            name=attachment.name,
            unit=call_input.unit,
            first=call_input.first,
            last=call_input.first + len(chosen) - 1,
            total=len(items),
            text=_joined(chosen, call_input.unit, call_input.first),
            cut=cut,
        )

    def _bound(self, unit: Unit) -> int:
        return self._options.max_pages if unit == "pages" else self._options.max_lines

    async def _find(self, ctx: TenantContext, session_id: UUID, attachment_id: UUID) -> Attachment:
        """The placeholder of `attachment_id` among the steps of the call's
        own session. None found is the same answer whether the id is another
        session's, another tenant's, or nobody's."""
        after = 0
        while True:
            page = await self._steps.get_steps(ctx, session_id, after, self._options.page)
            for step in page.items:
                for attachment in step.children.attachments:
                    if attachment.id == attachment_id:
                        return attachment
            if not page.has_more or not page.items:
                break
            after = page.items[-1].seq
        raise ToolFailed(
            ToolFailure.INVALID_INPUT,
            f"this session holds no attachment {attachment_id}; name one its file labels show",
        )


def _lines(pages: Sequence[str]) -> list[str]:
    """The file's lines across its pages; a final newline ends a line and
    starts none."""
    lines: list[str] = []
    for page in pages:
        split = page.split("\n")
        if split and split[-1] == "":
            split.pop()
        lines.extend(line.removesuffix("\r") for line in split)
    return lines


def _labelled(item: str, unit: Unit, number: int) -> str:
    return f"[page {number}]\n{item}" if unit == "pages" else item


def _joined(items: Sequence[str], unit: Unit, first: int) -> str:
    return "\n".join(_labelled(item, unit, first + n) for n, item in enumerate(items))


def _within(items: Sequence[str], unit: Unit, first: int, max_chars: int) -> tuple[list[str], bool]:
    """The items from the start of the range that fit the bound together,
    and whether the range was cut. A first item alone past the bound is
    kept up to it."""
    chosen: list[str] = []
    used = 0
    for n, item in enumerate(items):
        size = len(_labelled(item, unit, first + n)) + (1 if chosen else 0)
        if used + size > max_chars:
            if not chosen:
                chosen.append(item[: max(max_chars - len(_labelled("", unit, first)), 0)])
            return chosen, True
        chosen.append(item)
        used += size
    return chosen, False
