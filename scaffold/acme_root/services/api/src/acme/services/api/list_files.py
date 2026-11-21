"""`list_files`: the read tool of the example assistant. It lists the org's
stored files a page at a time, and never reads a file's bytes."""

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from acme.om.base import OmBase
from acme.om.context import TenantContext
from acme.om.media import MediaManagerInterface
from acme.om.media.types.file import FilePurpose
from acme.om.tools.tool import ToolInterface, ToolRuntime
from acme.om.tools.types.policy import Target
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolMode, ToolSpec

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
    files: tuple[FileSeen, ...] = ()
    has_more: bool = False


class ListFilesToolImpl(ToolInterface):
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
            mode=ToolMode.SYNC,
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
        seen = tuple(FileSeen.model_validate(file, from_attributes=True) for file in page.items)
        return FilesFound(files=seen, has_more=page.has_more)
