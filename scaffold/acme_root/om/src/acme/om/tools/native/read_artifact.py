"""`read_artifact`: a page of a result kept whole outside the window, by the
handle the window names. A result above the size bound is an artifact, and
the notice between its head and its tail names its id; a result elided once
read renders as a stub that names its artifact, or its step when it fit
one. A step's handle reads the whole result, its artifact's when it has
one. Either handle is looked up in the session the call was made in
(`ToolRuntime.session_id`), never one the input names, so a handle another
session holds answers as one that never existed. One answer is bounded as
the model reads it, its JSON with every quote and newline escaped, under
the window's result bound, so a page is kept in its step whole and never
becomes an artifact itself."""

from datetime import timedelta
from uuid import UUID

from pydantic import Field
from pydantic_core import to_json

from acme.om.base import Platform
from acme.om.context import TenantContext
from acme.om.exceptions import NotFound, ToolFailed
from acme.om.steps import StepsManagerInterface
from acme.om.steps.types.header import ToolFailure, ToolResponseHeader
from acme.om.steps.types.step import Step
from acme.om.tools.tool import ToolInterface, ToolRuntime
from acme.om.tools.types.policy import Target
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolMode, ToolSpec
from acme.om.windows import WindowsManagerInterface
from acme.om.windows.rules import READ_ARTIFACT, result_text

DESCRIPTION = (
    "Read a tool result or a report kept whole outside your context: one too "
    "large for its step, of which a notice shows only the head and the tail, "
    "or one elided once read, which a stub stands in for. Name it by the "
    "handle the notice or the stub gives, an artifact's id or a step's. Read "
    "from offset, in characters from its start, at most limit characters. "
    "The answer says how many characters the whole holds, and gives "
    "next_offset to read on from while more follow."
)


class ReadArtifactOptions(Platform):
    """The bound of one read. `max_chars` bounds the answer as the model
    reads it, its JSON with every quote and newline escaped, and stays under
    the window's result bound, so an answer is kept in its step whole."""

    max_chars: int = Field(default=20_000, ge=1_000)
    page: int = Field(default=200, gt=0)  # steps one read of the history asks for


class ReadArtifactInput(ToolInput):
    handle: UUID
    offset: int = Field(default=0, ge=0)  # characters from the start
    limit: int | None = Field(default=None, gt=0)  # None reads as much as one answer holds


class ArtifactRange(Platform):
    """What one read answers: the text kept under `handle` from `offset`, of
    the `characters` the whole holds. `next_offset` is where to read on
    from, there only while more follow."""

    handle: UUID
    offset: int
    text: str
    characters: int
    next_offset: int | None = Field(default=None, exclude_if=lambda offset: offset is None)


class ReadArtifactToolImpl(ToolInterface):
    def __init__(
        self,
        steps: StepsManagerInterface,
        windows: WindowsManagerInterface,
        options: ReadArtifactOptions | None = None,
    ) -> None:
        self._steps = steps
        self._windows = windows
        self._options = options or ReadArtifactOptions()
        self._spec = ToolSpec(
            name=READ_ARTIFACT,
            description=DESCRIPTION,
            input_model=ReadArtifactInput,
            output_model=ArtifactRange,
            timeout=timedelta(minutes=1),
            authorization_class=ToolClass.READ,
            effect=Effect.READ_ONLY,
            interruptible=True,
            mode=ToolMode.SYNC,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def target(self, ctx: TenantContext, call_input: ToolInput) -> Target:
        return Target()

    async def preflight(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> None:
        return None

    async def run(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> Platform:
        assert isinstance(call_input, ReadArtifactInput)
        handle, offset = call_input.handle, call_input.offset
        want = min(call_input.limit or self._options.max_chars, self._options.max_chars)
        text, characters = await self._page(ctx, runtime.session_id, handle, offset, want)
        if offset >= characters:
            raise ToolFailed(
                ToolFailure.INVALID_INPUT,
                f"{handle} holds {characters} characters; offset {offset} is at or past its end",
            )
        # What the answer holds beside its text, at its widest: an offset to
        # read on from, as long as the whole's length.
        widest = ArtifactRange(
            handle=handle, offset=offset, text="", characters=characters, next_offset=characters
        )
        text = text[: _prefix(text, self._options.max_chars - len(widest.model_dump_json()))]
        end = offset + len(text)
        return ArtifactRange(
            handle=handle,
            offset=offset,
            text=text,
            characters=characters,
            next_offset=end if end < characters else None,
        )

    async def _page(
        self, ctx: TenantContext, session_id: UUID, handle: UUID, offset: int, want: int
    ) -> tuple[str, int]:
        """The text kept under `handle` from `offset`, at most `want`
        characters, and how many the whole holds: an artifact of the
        session, or a tool result among its steps, read from its artifact
        when it has one."""
        try:
            page = await self._windows.get_artifact(ctx, session_id, handle, offset, want)
            return page.text, page.characters
        except NotFound:
            pass
        step = await self._find(ctx, session_id, handle)
        header = step.header
        assert isinstance(header, ToolResponseHeader)
        if header.artifact is not None:
            page = await self._windows.get_artifact(
                ctx, session_id, header.artifact.id, offset, want
            )
            return page.text, page.characters
        whole = result_text(step.as_tool_response())
        return whole[offset : offset + want], len(whole)

    async def _find(self, ctx: TenantContext, session_id: UUID, handle: UUID) -> Step:
        """The tool response `handle` names among the steps of the call's own
        session. None found is the same answer whether the handle is another
        session's, another tenant's, or nobody's."""
        after = 0
        while True:
            page = await self._steps.get_steps(ctx, session_id, after, self._options.page)
            for step in page.items:
                if step.id == handle and isinstance(step.header, ToolResponseHeader):
                    return step
            if not page.has_more or not page.items:
                break
            after = page.items[-1].seq
        raise ToolFailed(
            ToolFailure.INVALID_INPUT,
            f"this session keeps no result under {handle}; name the handle a notice or a "
            "stub gives",
        )


def _escaped(text: str) -> int:
    """The characters `text` takes in the answer's JSON, its quotes aside."""
    return len(to_json(text).decode()) - 2


def _prefix(text: str, room: int) -> int:
    """The longest start of `text` whose escaped form fits `room`."""
    if _escaped(text) <= room:
        return len(text)
    fits, past = 0, len(text)
    while past - fits > 1:
        middle = (fits + past) // 2
        if _escaped(text[:middle]) <= room:
            fits = middle
        else:
            past = middle
    return fits
