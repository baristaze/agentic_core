"""A model response keeps its thinking where its turn had it: a step holds
the thinking apart from its blocks, each block naming its place, and reads
the turn back in the provider's order through storage's JSON."""

from contracts.step_storage import make_response

from acme.integrations.model_providers.content import (
    TextBlock,
    ThinkingBlock,
    ThinkingSource,
    ToolUseBlock,
)
from acme.integrations.model_providers.types import ProviderName
from acme.om.base import new_id
from acme.om.steps.types.content import Children, Content
from acme.om.steps.types.step import Step

SOURCE = ThinkingSource(provider=ProviderName.ANTHROPIC, model="claude-haiku-4-5")


def test_a_response_step_keeps_its_thinking_in_place() -> None:
    session, loop = new_id(), new_id()
    blocks = (
        TextBlock(text="I'll add both pairs."),
        ToolUseBlock(id="toolu_a", name="add", input={"a": 17, "b": 25}),
        ToolUseBlock(id="toolu_b", name="add", input={"a": 8, "b": 13}),
    )
    thinking = tuple(
        ThinkingBlock(text=f"thought {at}", signature=f"sig-{at}", source=SOURCE, at=at)
        for at in (0, 1, 2)
    )
    response = make_response(session, loop, new_id())
    step = response.model_copy(
        update={"content": Content(blocks=blocks), "children": Children(thinking=thinking)}
    )
    stored = Step.model_validate(step.model_dump(mode="json"))
    assert stored == step
    assert [getattr(b, "id", None) or getattr(b, "text", "") for b in stored.as_turn()] == [
        "thought 0",
        "I'll add both pairs.",
        "thought 1",
        "toolu_a",
        "thought 2",
        "toolu_b",
    ]
