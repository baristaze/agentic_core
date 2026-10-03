from uuid import UUID

from acme.om.context import TenantContext
from acme.om.exceptions import Unavailable
from acme.om.steps.types.content import Attachment
from acme.om.tools.attachments import AttachmentReaderInterface, AttachmentText


class AttachmentReaderNullImpl(AttachmentReaderInterface):
    """The reader of a root that wired no attachment store. It is loud: the
    call is refused and says why, never answered as an empty file."""

    async def read_text(
        self, ctx: TenantContext, session_id: UUID, attachment: Attachment
    ) -> AttachmentText | None:
        raise Unavailable("no attachment store is wired, so no attachment is read")
