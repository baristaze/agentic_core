from uuid import UUID

from acme.om.context import TenantContext
from acme.om.exceptions import Unavailable
from acme.om.tools.seal import RecordSealInterface


class RecordSealNullImpl(RecordSealInterface):
    """The seal of a root that wired no key service. It is loud: an output kept
    in the clear would outlive the revocation that erases its session's
    content, so it refuses and says why, and the record keeps how the
    command ended alone."""

    async def seal(
        self, ctx: TenantContext, session_id: UUID, key: UUID, data: bytes
    ) -> bytes | None:
        raise Unavailable("no key service is wired, so no command's output is kept")

    async def open(
        self, ctx: TenantContext, session_id: UUID, key: UUID, sealed: bytes
    ) -> bytes | None:
        raise Unavailable("no key service is wired, so no command's output is opened")
