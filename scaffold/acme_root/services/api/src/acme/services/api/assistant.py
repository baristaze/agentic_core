"""The example assistant the local and the staging stacks offer: its kind,
a folder of its own (`kinds/assistant/`), and its one read tool
(`list_files`). A product that declares kinds of its own is offered none
of it."""

from collections.abc import Callable

from acme.om.agents.types.kind import AgentKind
from acme.om.media import MediaManagerInterface
from acme.om.tools.tool import ToolInterface
from acme.services.api.kinds.assistant.kind import ASSISTANT
from acme.services.api.list_files import ListFilesToolImpl

OFFERED_IN = frozenset({"local", "staging"})
"""The environments whose API offers the example assistant."""


def offered(
    environment: str,
    declared: tuple[AgentKind, ...],
    media: Callable[[], MediaManagerInterface],
) -> tuple[tuple[AgentKind, ...], tuple[ToolInterface, ...]]:
    """The example's kind and its tool where `environment` offers them and
    the product declares no kind of its own, and nothing elsewhere: a
    product's kinds take the example's place. `media` answers the manager
    the tool reads, once the root that builds it stands."""
    if environment not in OFFERED_IN or declared:
        return (), ()
    return (ASSISTANT,), (ListFilesToolImpl(media),)
