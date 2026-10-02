"""A session's history built in memory, step by step, numbered as storage
numbers it: what the windows suites render, cut, and compact without a
store. Every step is a real `Step`, so each one passes its type's check."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from acme.integrations.model_providers.types import Usage
from acme.om.base import new_id
from acme.om.models.types.fill import MAIN, SUMMARIZER
from acme.om.steps.types.content import (
    Content,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    InputHeader,
    MarkHeader,
    ModelRequestHeader,
    ModelResponseHeader,
    SummaryHeader,
    ToolRequestHeader,
    ToolResponseHeader,
)
from acme.om.steps.types.step import Actor, Origin, Step, StepType

AT = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
MAIN_FILL = "anthropic/claude-sonnet-5-5"


class History:
    """Appends steps in order; each takes the next `seq`."""

    def __init__(self, session_id: UUID | None = None) -> None:
        self.session_id = session_id or new_id()
        self.steps: list[Step] = []
        self.loop_id: UUID | None = None

    def add(self, **fields: Any) -> Step:
        step_id = new_id()
        if self.loop_id is None:
            self.loop_id = step_id
        step = Step(
            id=step_id,
            created_at=AT,
            session_id=self.session_id,
            seq=len(self.steps) + 1,
            loop_id=self.loop_id,
            **fields,
        )
        self.steps.append(step)
        return step

    def message(
        self, text: str, actor: Actor = Actor.PERSON, origin: Origin = Origin.PORTAL
    ) -> Step:
        return self.add(
            type=StepType.MESSAGE,
            actor=actor,
            origin=origin,
            header=InputHeader(),
            content=Content(blocks=(TextBlock(text=text),)),
        )

    def event(self, text: str) -> Step:
        return self.add(
            type=StepType.EVENT,
            actor=Actor.EXTERNAL,
            origin=Origin.INTEGRATION,
            header=InputHeader(),
            content=Content(blocks=(TextBlock(text=text),)),
        )

    def changed(self, text: str) -> Step:
        return self.add(
            type=StepType.ENVIRONMENT_CHANGED,
            actor=Actor.ENGINE,
            origin=Origin.ENGINE,
            header=MarkHeader(),
            content=Content(blocks=(TextBlock(text=text),)),
        )

    def control(self, command: ControlCommand) -> Step:
        return self.add(
            type=StepType.CONTROL,
            actor=Actor.PERSON,
            origin=Origin.PORTAL,
            header=ControlHeader(command=command),
        )

    def request(
        self,
        delivers: Sequence[Step] = (),
        role: str = MAIN,
        fill: str = MAIN_FILL,
        summary_id: UUID | None = None,
    ) -> Step:
        return self.add(
            type=StepType.MODEL_REQUEST,
            actor=Actor.ENGINE,
            origin=Origin.ENGINE,
            refs=tuple(step.id for step in delivers),
            header=ModelRequestHeader(
                role=role,
                fill=fill,
                fill_set_version=1,
                left_edge=1,
                summary_id=summary_id,
                prompt_hash="k:recorded",
            ),
        )

    def response(
        self,
        request: Step,
        text: str = "",
        uses: Sequence[tuple[str, str, Mapping[str, Any]]] = (),
        *,
        thinking: str | None = None,
        usage: Usage | None = None,
        truncated: bool = False,
        abandoned: bool = False,
    ) -> Step:
        blocks: list[TextBlock | ToolUseBlock] = [TextBlock(text=text)] if text else []
        blocks += [ToolUseBlock(id=i, name=name, input=dict(given)) for i, name, given in uses]
        thoughts = (ThinkingBlock(text=thinking, signature="sig", at=0),) if thinking else ()
        return self.add(
            type=StepType.MODEL_RESPONSE,
            actor=Actor.MODEL,
            origin=Origin.ENGINE,
            responds_to=request.id,
            header=ModelResponseHeader(truncated=truncated, abandoned=abandoned, usage=usage),
            content=Content(blocks=tuple(blocks)),
            children={"thinking": thoughts},
        )

    def call(self, response: Step, use_id: str, tool: str = "read_log") -> Step:
        return self.add(
            type=StepType.TOOL_REQUEST,
            actor=Actor.ENGINE,
            origin=Origin.ENGINE,
            refs=(response.id,),
            header=ToolRequestHeader(tool=tool, tool_use_id=use_id, input_hash=f"k:{use_id}"),
        )

    def result(self, call: Step, text: str, *, error: bool = False) -> Step:
        header = call.header
        assert isinstance(header, ToolRequestHeader)
        result = ToolResultBlock(
            tool_use_id=header.tool_use_id, parts=(TextBlock(text=text),), is_error=error
        )
        return self.add(
            type=StepType.TOOL_RESPONSE,
            actor=Actor.ENGINE,
            origin=Origin.ENGINE,
            responds_to=call.id,
            header=ToolResponseHeader(),
            content=Content(blocks=(result,)),
        )

    def turn(
        self, delivers: Sequence[Step], text: str, results: Sequence[tuple[str, str]] = ()
    ) -> Step:
        """A whole exchange: a request delivering `delivers`, its response
        calling a tool for each of `results`, and each result in order."""
        request = self.request(delivers)
        uses = [(f"call_{len(self.steps)}_{n}", tool, {}) for n, (tool, _) in enumerate(results)]
        response = self.response(request, text, uses)
        for (use_id, tool, _), (_, said) in zip(uses, results, strict=True):
            self.result(self.call(response, use_id, tool), said)
        return response

    def summarize(self, through_seq: int, text: str, first_seq: int = 1) -> Step:
        """A compaction as the engine writes it: the summarizer's request and
        reply, and the summary that references the reply and its range."""
        request = self.request(role=SUMMARIZER, fill="anthropic/claude-haiku-4-5")
        reply = self.response(request, text)
        return self.add(
            type=StepType.SUMMARY,
            actor=Actor.ENGINE,
            origin=Origin.ENGINE,
            refs=(reply.id,),
            header=SummaryHeader(first_seq=first_seq, last_seq=through_seq),
        )
