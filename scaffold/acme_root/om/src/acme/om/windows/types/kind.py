"""What the first two layers of a request hold: an agent kind's prompts at
one version, the session's standing text, and the tools it offers, those a
load added after the rest. The agent kind is a profile of its own
namespace; a request reads only these, so a render takes them as a
value."""

from pydantic import Field

from acme.integrations.model_providers.calls import OutputSchema, ToolSpec
from acme.om.base import Platform


class KindPrompts(Platform):
    """An agent kind's prompts, in order, at `version`; the standing text its
    session pinned, which renders after them, empty for none; the tools it
    offers, rendered sorted by name, then those its session's loads added,
    in the order they were added; and the schema of an answer, for a fill
    whose output is one."""

    kind: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    prompts: tuple[str, ...] = ()
    standing: str = ""
    tools: tuple[ToolSpec, ...] = ()
    loaded: tuple[ToolSpec, ...] = ()
    output_schema: OutputSchema | None = None
