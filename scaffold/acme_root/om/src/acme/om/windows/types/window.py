"""A context window, and the request rendered over it.

A window is a property of a model request, never of the history: steps
carry no window id. It holds references and sizes, never content, so it is
rebuilt from the steps and the latest summary whenever it is needed."""

from uuid import UUID

from pydantic import Field

from acme.integrations.model_providers.calls import ModelCall
from acme.om.attribution.types.authority import RequestAttribution
from acme.om.base import Platform
from acme.om.models.types.fill import ModelRole
from acme.om.steps.types.content import Attachment


class ContextWindow(Platform):
    """The steps one request of `role` reads, by reference: the fill it is
    sized for, the fill's window (`max_tokens`), what the window holds
    (`used_tokens`: the size the provider reported for the last call over
    this window, plus an estimate for the steps since), the first step it
    reads verbatim (`left_edge`), the last it reads (`right_edge`, 0 when
    none), and the summary it reads before them."""

    role: ModelRole
    fill: str = Field(min_length=1, max_length=200)
    fill_set_version: int = Field(ge=1)
    max_tokens: int = Field(gt=0)
    used_tokens: int = Field(ge=0)
    left_edge: int = Field(ge=1)
    right_edge: int = Field(ge=0)
    summary_id: UUID | None = None


class RenderedRequest(Platform):
    """A request as the renderer made it. `delivers` are the new inputs it
    carries, which its step references; `attachments` are the placeholders
    whose bytes the call takes from blob storage; `prompt_hash` is keyed by
    the session. `overflow_retry` marks the one render a request gets after
    the provider refused it as too long. `attribution` is who spoke and who
    pays, as attribution answered for exactly the inputs it delivers, after
    the latest model request it was rendered from."""

    call: ModelCall
    window: ContextWindow
    delivers: tuple[UUID, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    prompt_hash: str = Field(min_length=1)
    overflow_retry: bool = False
    attribution: RequestAttribution
