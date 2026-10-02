"""What the first two layers of a request hold: an agent kind's prompts at
one version, and the tools it offers. The agent kind is a profile of its
own namespace; a request reads only these, so a render takes them as a
value."""

from pydantic import Field

from acme.integrations.model_providers.calls import OutputSchema, ToolSpec
from acme.om.base import Platform


class KindPrompts(Platform):
    """An agent kind's prompts, in order, at `version`; the tools it offers,
    rendered sorted by name; and the schema of an answer, for a fill whose
    output is one."""

    kind: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    prompts: tuple[str, ...] = ()
    tools: tuple[ToolSpec, ...] = ()
    output_schema: OutputSchema | None = None
