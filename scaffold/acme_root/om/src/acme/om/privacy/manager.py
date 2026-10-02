"""The privacy swimlane: where a session's content lives, the key it is
sealed under, and the erasure of that content by revoking the key.

Content is sealed per session, and shape is not. The sealing itself is a
layer under step storage, which the root wires and the rest of the engine
never sees; this manager holds what a principal decides about it: the
storage policy chosen for a session, a new version of its key, the
revocation that erases its content and keeps its record, and the engine's
half of a rotation of the tenant's wrapping key."""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from acme.om.context import TenantContext
from acme.om.privacy.types.session_privacy import SessionPrivacy, StoragePolicy


class PrivacyManagerInterface(ABC):
    @abstractmethod
    async def get_privacy(self, ctx: TenantContext, session_id: UUID) -> SessionPrivacy:
        """The session's record; a session with none is answered the
        default, sealed and never revoked. `NotFound` for a session the
        tenant does not hold."""
        ...

    @abstractmethod
    async def set_policy(
        self, ctx: TenantContext, session_id: UUID, policy: StoragePolicy
    ) -> SessionPrivacy:
        """The session's storage policy, chosen once, before its history
        begins, and announced. The same policy chosen again is answered as
        stored; another is `PolicyFixed`, and so is any policy once a run or
        a step has begun the history."""
        ...

    @abstractmethod
    async def revoke_key(self, ctx: TenantContext, session_id: UUID) -> SessionPrivacy:
        """Erases the session's content: every version of its key is
        destroyed, and the session takes no content again. Its steps keep
        their place, their type, and their shape, and read as absent. Once,
        and announced; a revoked session is answered as it is."""
        ...

    @abstractmethod
    async def rotate_key(self, ctx: TenantContext, session_id: UUID) -> int:
        """A new version of the session's key, which new content is sealed
        under from now on; content sealed before keeps its version. Returns
        the version. `KeyRevoked` when the key is revoked."""
        ...

    @abstractmethod
    async def rewrap_keys(self, ctx: TenantContext, before: datetime) -> int:
        """The engine's half of a rotation of the tenant's wrapping key: a
        batch of the tenant's key versions wrapped before `before`, each
        wrapped again under the current wrapping key. No content is read or
        written. Returns how many; a whole batch says there may be more."""
        ...

    @abstractmethod
    async def keyed_hash(self, ctx: TenantContext, session_id: UUID, value: bytes) -> str:
        """A hash of `value` keyed by the session, for the shape to carry in
        place of content: two equal values in one session hash alike, and
        once the session's key is revoked nothing can confirm what a hash
        stood for. `KeyRevoked` when it is."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: TenantContext) -> int:
        """The sweep, for one tenant past its own retention: its records and
        its key versions, a batch at most a call. Any other tenant returns 0
        and reads nothing."""
        ...
