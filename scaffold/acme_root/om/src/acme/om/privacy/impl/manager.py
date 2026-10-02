import hashlib
import hmac
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.base import Platform, new_id, utcnow
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import PolicyFixed
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow, outbox_row
from acme.om.privacy.keys import SessionKeysInterface
from acme.om.privacy.manager import PrivacyManagerInterface
from acme.om.privacy.storage import PrivacyStorageInterface
from acme.om.privacy.types.session_privacy import SessionPrivacy, StoragePolicy
from acme.om.steps import StepsManagerInterface
from acme.om.tenancy import TenancyManagerInterface

CREATED = "privacy.session_privacy.created"
UPDATED = "privacy.session_privacy.updated"


class PrivacyOptions(Platform):
    rewrap_batch: int = 100  # key versions one rewrap moves at most
    purge_batch: int = 1000  # rows one purge statement deletes at most


class PrivacyManagerImpl(PrivacyManagerInterface):
    def __init__(
        self,
        storage: PrivacyStorageInterface,
        keys: SessionKeysInterface,
        steps: StepsManagerInterface,
        sessions: AgentSessionsManagerInterface,
        tenancy: TenancyManagerInterface,
        relay: OutboxRelayInterface,
        options: PrivacyOptions,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._storage = storage
        self._keys = keys
        self._steps = steps
        self._sessions = sessions
        self._tenancy = tenancy
        self._relay = relay
        self._options = options
        self._clock = clock

    async def get_privacy(self, ctx: TenantContext, session_id: UUID) -> SessionPrivacy:
        ctx.require(Permission.READ)
        session = await self._sessions.get_session(ctx, session_id)
        stored = await self._storage.read_privacy(ctx.org_id, session_id)
        return stored or SessionPrivacy(id=new_id(), session_id=session_id, created_at=session.created_at)

    async def set_policy(
        self, ctx: TenantContext, session_id: UUID, policy: StoragePolicy
    ) -> SessionPrivacy:
        ctx.require(Permission.WRITE)
        await self._sessions.get_session(ctx, session_id)
        cursor = await self._steps.get_cursor(ctx, session_id)
        if cursor.head or cursor.epoch:
            stored = await self._storage.read_privacy(ctx.org_id, session_id)
            return self._chosen(session_id, stored, policy)
        record = SessionPrivacy(
            id=new_id(), session_id=session_id, created_at=self._clock(), policy=policy
        )
        rows = (outbox_row(ctx, CREATED, session_id, {}),)
        stored = await self._storage.create_privacy(ctx.org_id, record, rows)
        if stored is record:
            await self._relay_all(ctx, rows)
            return stored
        return self._chosen(session_id, stored, policy)

    @staticmethod
    def _chosen(
        session_id: UUID, stored: SessionPrivacy | None, policy: StoragePolicy
    ) -> SessionPrivacy:
        """The stored record, when it holds this policy; otherwise another
        was chosen, or the history began under the default."""
        if stored is None or stored.policy != policy:
            raise PolicyFixed(f"the storage policy of session {session_id} is chosen")
        return stored

    async def revoke_key(self, ctx: TenantContext, session_id: UUID) -> SessionPrivacy:
        ctx.require(Permission.WRITE)
        await self._sessions.get_session(ctx, session_id)
        now = self._clock()
        record = SessionPrivacy(
            id=new_id(),
            session_id=session_id,
            created_at=now,
            revoked_at=now,
            revoked_by=ctx.user_id,
        )
        rows = (outbox_row(ctx, UPDATED, session_id, {"revoked": True}),)
        stored = await self._storage.revoke(ctx.org_id, record, rows)
        if stored.revoked_at == now and stored.revoked_by == ctx.user_id:
            await self._relay_all(ctx, rows)
        return stored

    async def rotate_key(self, ctx: TenantContext, session_id: UUID) -> int:
        ctx.require(Permission.WRITE)
        await self._sessions.get_session(ctx, session_id)
        return await self._keys.add_version(ctx.org_id, session_id)

    async def rewrap_keys(self, ctx: TenantContext, before: datetime) -> int:
        ctx.require(Permission.WRITE)
        return await self._keys.rewrap(ctx.org_id, before, self._options.rewrap_batch)

    async def keyed_hash(self, ctx: TenantContext, session_id: UUID, value: bytes) -> str:
        ctx.require(Permission.WRITE)
        key = await self._keys.hash_key(ctx.org_id, session_id)
        return hmac.new(key, value, hashlib.sha256).hexdigest()

    async def purge_tenant(self, ctx: TenantContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        return await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

    async def _relay_all(self, ctx: TenantContext, rows: tuple[OutboxRow, ...]) -> None:
        """The write has committed; a relay that fails is left to the sweep."""
        await self._relay.relay_all(ctx.org_id, rows)
