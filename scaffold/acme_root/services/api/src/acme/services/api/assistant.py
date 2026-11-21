"""The example assistant the local and the staging stacks offer: a kind
whose loop runs in the request that asks it (ADR 1044), answering a
member's questions about the org's files through one read tool, which no
person approves. A copy replaces it with its own kinds and tools, which
its roots declare as this one is declared here."""

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import Field

from acme.om.agent_sessions.limits import Limits
from acme.om.agents.types.kind import REQUEST_STEPS, REQUEST_TIME, AgentKind, DoneRule, RunsIn
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.base import OmBase
from acme.om.context import TenantContext
from acme.om.media import MediaManagerInterface
from acme.om.media.types.file import FilePurpose
from acme.om.tools.tool import ToolInterface, ToolRuntime
from acme.om.tools.types.policy import Decision, Target, ToolPolicyLayer, ToolPolicyRule
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolSpec

OFFERED_IN = frozenset({"local", "staging"})
"""The environments whose API offers the example assistant."""

LIST_FILES = "list_files"
PAGE = 50


class FilesAsked(ToolInput):
    after: UUID | None = None  # the last file of the page before


class FileSeen(OmBase):
    id: UUID
    name: str
    content_type: str
    size_bytes: int
    created_at: datetime


class FilesFound(OmBase):
    files: list[FileSeen] = Field(default_factory=lambda: [])
    has_more: bool = False


class ListFiles(ToolInterface):
    """The org's stored files, a page at a time, oldest first: what each is
    called, its type, its size, and when it came. Never a file's bytes."""

    def __init__(self, media: Callable[[], MediaManagerInterface]) -> None:
        self._media = media
        self._spec = ToolSpec(
            name=LIST_FILES,
            description=(
                "List the files the org keeps, oldest first, a page at a time: each "
                "one's id, name, type, size in bytes, and when it was uploaded. "
                "Pass the id of the last file you read as `after` for the next page."
            ),
            input_model=FilesAsked,
            output_model=FilesFound,
            timeout=timedelta(seconds=10),
            authorization_class=ToolClass.READ,
            effect=Effect.READ_ONLY,
            interruptible=True,
            workspace=False,
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

    async def run(self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime) -> OmBase:
        assert isinstance(call_input, FilesAsked)
        page = await self._media().get_files(ctx, FilePurpose.UPLOAD, None, call_input.after, PAGE)
        seen = [FileSeen.model_validate(file, from_attributes=True) for file in page.items]
        return FilesFound(files=seen, has_more=page.has_more)


ASSISTANT = AgentKind(
    name="assistant",
    version=1,
    tools=(LIST_FILES,),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    prompts=(
        "You answer a member's questions about the files their org keeps. Read "
        "them with list_files, and answer in a few sentences. When the files do "
        "not hold the answer, say so.",
    ),
    policy=ToolPolicyLayer(
        rules=(ToolPolicyRule(authorization_class=ToolClass.READ, decision=Decision.ALLOW),)
    ),
    limits=Limits(step_guard=REQUEST_STEPS, run_time=REQUEST_TIME),
    runs_in=RunsIn.REQUEST,
)


def offered(
    environment: str, media: Callable[[], MediaManagerInterface]
) -> tuple[tuple[AgentKind, ...], tuple[ToolInterface, ...]]:
    """The example's kind and its tool where `environment` offers them, and
    nothing elsewhere. `media` answers the manager the tool reads, once the
    root that builds it stands."""
    if environment not in OFFERED_IN:
        return (), ()
    return (ASSISTANT,), (ListFiles(media),)
