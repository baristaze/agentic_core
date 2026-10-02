import hashlib
import hmac
import secrets
from uuid import UUID

from acme.om.context import TenantContext
from acme.om.exceptions import Unavailable
from acme.om.windows.hashes import PromptHashInterface


class PromptHashNullImpl(PromptHashInterface):
    """The hash of a root that wired no key service. It is loud: an unkeyed
    hash in a request's header would outlive the session's content, so it
    refuses and says why."""

    async def keyed_hash(self, ctx: TenantContext, session_id: UUID, value: bytes) -> str:
        raise Unavailable("no key service is wired, so no prompt is hashed")


class PromptHashMemoryImpl(PromptHashInterface):
    """Keys held in this process, one per tenant and session, made on first
    use: the key service's memory twin, for a process and a test. A key is
    never written anywhere, so it ends with the process."""

    def __init__(self) -> None:
        self._keys: dict[tuple[UUID, UUID], bytes] = {}

    async def keyed_hash(self, ctx: TenantContext, session_id: UUID, value: bytes) -> str:
        key = self._keys.setdefault((ctx.org_id, session_id), secrets.token_bytes(32))
        return hmac.new(key, value, hashlib.sha256).hexdigest()
