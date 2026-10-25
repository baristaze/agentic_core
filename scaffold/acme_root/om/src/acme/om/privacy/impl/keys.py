import os
from collections.abc import Callable, Collection, Set
from datetime import datetime
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from acme.infra.keys import KeyServiceInterface, WrappedKey
from acme.om.base import new_id, utcnow
from acme.om.exceptions import KeyRevoked
from acme.om.privacy.impl.blobs import VERSION_BYTES, Bound, open_blob, seal_blob
from acme.om.privacy.keys import AgentSessionKeysInterface, OpenKey
from acme.om.privacy.storage import PrivacyStorageInterface
from acme.om.privacy.types.agent_session_privacy import (
    AgentSessionKey,
    AgentSessionPrivacy,
    TenantTitleKey,
)

HASH_KEY_INFO = b"keyed hash"
"""What tells the hash key apart from the data key it is derived from."""

TITLE_KEY_BYTES = 32
"""A title key: an AES-256 key of its own, made at random."""

TENANT_TITLE_KEY_VERSION = 1
"""The tenant's title key has one version: a rotation wraps it again, and
what it wraps is never rewritten."""


def title_key_bound(org_id: UUID, agent_session_id: UUID) -> Bound:
    """What a wrapped title key is bound to: the tenant and the session, so
    one moved to another record, or another tenant, opens nothing."""
    return lambda version: (
        b"session title key"
        + org_id.bytes
        + agent_session_id.bytes
        + version.to_bytes(VERSION_BYTES, "big")
    )


class AgentSessionKeysImpl(AgentSessionKeysInterface):
    def __init__(
        self,
        storage: PrivacyStorageInterface,
        service: KeyServiceInterface,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._storage = storage
        self._service = service
        self._clock = clock

    async def current(self, org_id: UUID, agent_session_id: UUID) -> OpenKey:
        ring = await self._storage.read_keys(org_id, agent_session_id)
        if ring.revoked:
            raise KeyRevoked(f"the key of session {agent_session_id} is revoked")
        key = ring.current()
        if key is None:
            return await self._made(org_id, agent_session_id, 1)
        return OpenKey(version=key.version, key=await self._unwrap(org_id, key))

    async def opened(
        self, org_id: UUID, agent_session_id: UUID, versions: Set[int]
    ) -> dict[int, bytes]:
        if not versions:
            return {}
        ring = await self._storage.read_keys(org_id, agent_session_id)
        keys: dict[int, bytes] = {}
        for version in sorted(versions):
            key = ring.version(version)
            if key is not None and not key.is_destroyed():
                keys[version] = await self._unwrap(org_id, key)
        return keys

    async def add_version(self, org_id: UUID, agent_session_id: UUID) -> int:
        ring = await self._storage.read_keys(org_id, agent_session_id)
        if ring.revoked:
            raise KeyRevoked(f"the key of session {agent_session_id} is revoked")
        latest = ring.current()
        made = await self._made(
            org_id, agent_session_id, 1 if latest is None else latest.version + 1
        )
        return made.version

    async def hash_key(self, org_id: UUID, agent_session_id: UUID) -> bytes:
        ring = await self._storage.read_keys(org_id, agent_session_id)
        if ring.revoked:
            raise KeyRevoked(f"the key of session {agent_session_id} is revoked")
        first = ring.version(1)
        if first is None:
            key = (await self._made(org_id, agent_session_id, 1)).key
        else:
            key = await self._unwrap(org_id, first)
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=HASH_KEY_INFO).derive(key)

    async def title_key(self, org_id: UUID, agent_session_id: UUID) -> bytes:
        found = await self._storage.read_title_keys(org_id, (agent_session_id,))
        tenant = await self._tenant_title_key(org_id, found.tenant)
        bound = title_key_bound(org_id, agent_session_id)
        what = f"the title key of session {agent_session_id}"
        wrapped = found.sessions.get(agent_session_id)
        if wrapped is not None:
            return open_blob(tenant.key, wrapped, bound, what)
        made = os.urandom(TITLE_KEY_BYTES)
        wrapped = seal_blob(tenant, made, bound)
        stored = await self._storage.add_title_key(
            org_id,
            AgentSessionPrivacy(
                id=new_id(),
                agent_session_id=agent_session_id,
                created_at=self._clock(),
                chosen=False,
                title_key=wrapped,
            ),
        )
        if stored.title_key is None:
            raise KeyRevoked(f"the key of session {agent_session_id} is revoked")
        if stored.title_key == wrapped:
            return made
        return open_blob(tenant.key, stored.title_key, bound, what)

    async def title_keys(
        self, org_id: UUID, agent_session_ids: Collection[UUID]
    ) -> dict[UUID, bytes]:
        if not agent_session_ids:
            return {}
        found = await self._storage.read_title_keys(org_id, agent_session_ids)
        if found.tenant is None or not found.sessions:
            return {}
        tenant = await self._unwrap_title_key(org_id, found.tenant)
        return {
            agent_session_id: open_blob(
                tenant,
                wrapped,
                title_key_bound(org_id, agent_session_id),
                f"the title key of session {agent_session_id}",
            )
            for agent_session_id, wrapped in found.sessions.items()
        }

    async def rewrap(self, org_id: UUID, before: datetime, limit: int) -> int:
        now = self._clock()
        moved = await self._rewrap_title_key(org_id, min(before, now), now)
        stale = await self._storage.read_keys_wrapped_before(org_id, min(before, now), limit)
        for key in stale:
            if key.wrapped is None or key.wrapping is None:
                continue
            wrapped = await self._service.rewrap(
                org_id,
                key.agent_session_id,
                key.version,
                WrappedKey(blob=key.wrapped, wrapping=key.wrapping),
            )
            rewrapped = key.model_copy(
                update={"wrapped": wrapped.blob, "wrapping": wrapped.wrapping, "wrapped_at": now}
            )
            if await self._storage.rewrap_key(org_id, rewrapped, key.wrapped):
                moved += 1
        return moved

    async def _rewrap_title_key(self, org_id: UUID, before: datetime, now: datetime) -> int:
        """The tenant's title key wrapped again when it was wrapped before
        `before`: 1 when it moved."""
        key = (await self._storage.read_title_keys(org_id, ())).tenant
        if key is None or key.wrapped_at >= before:
            return 0
        wrapped = await self._service.rewrap(
            org_id,
            key.id,
            TENANT_TITLE_KEY_VERSION,
            WrappedKey(blob=key.wrapped, wrapping=key.wrapping),
        )
        rewrapped = key.model_copy(
            update={"wrapped": wrapped.blob, "wrapping": wrapped.wrapping, "wrapped_at": now}
        )
        return int(await self._storage.rewrap_tenant_title_key(org_id, rewrapped, key.wrapped))

    async def _tenant_title_key(self, org_id: UUID, found: TenantTitleKey | None) -> OpenKey:
        """The tenant's title key in the clear, for the call in hand: `found`
        unwrapped, or one made, wrapped, and kept when the tenant has none. A
        call that made one first wins, and its key is the one answered."""
        if found is not None:
            return OpenKey(
                version=TENANT_TITLE_KEY_VERSION, key=await self._unwrap_title_key(org_id, found)
            )
        now = self._clock()
        made_id = new_id()
        data = await self._service.generate(org_id, made_id, TENANT_TITLE_KEY_VERSION)
        made = TenantTitleKey(
            id=made_id,
            created_at=now,
            wrapped=data.wrapped.blob,
            wrapping=data.wrapped.wrapping,
            wrapped_at=now,
        )
        stored = await self._storage.add_tenant_title_key(org_id, made)
        if stored.id == made.id:
            return OpenKey(version=TENANT_TITLE_KEY_VERSION, key=data.plaintext)
        return OpenKey(
            version=TENANT_TITLE_KEY_VERSION, key=await self._unwrap_title_key(org_id, stored)
        )

    async def _unwrap_title_key(self, org_id: UUID, key: TenantTitleKey) -> bytes:
        return await self._service.unwrap(
            org_id,
            key.id,
            TENANT_TITLE_KEY_VERSION,
            WrappedKey(blob=key.wrapped, wrapping=key.wrapping),
        )

    async def _made(self, org_id: UUID, agent_session_id: UUID, version: int) -> OpenKey:
        """Version `version`, made, wrapped, and kept, under the session's
        record, which the default writes when the session has none. A call
        that made the same version first wins, and its key is the one
        answered."""
        now = self._clock()
        await self._storage.create_privacy(
            org_id,
            AgentSessionPrivacy(id=new_id(), agent_session_id=agent_session_id, created_at=now),
        )
        data = await self._service.generate(org_id, agent_session_id, version)
        made = AgentSessionKey(
            id=new_id(),
            created_at=now,
            agent_session_id=agent_session_id,
            version=version,
            wrapped=data.wrapped.blob,
            wrapping=data.wrapped.wrapping,
            wrapped_at=now,
        )
        stored = await self._storage.add_key(org_id, made)
        if stored.id == made.id:
            return OpenKey(version=version, key=data.plaintext)
        return OpenKey(version=stored.version, key=await self._unwrap(org_id, stored))

    async def _unwrap(self, org_id: UUID, key: AgentSessionKey) -> bytes:
        if key.wrapped is None or key.wrapping is None:
            raise KeyRevoked(
                f"version {key.version} of session {key.agent_session_id} is destroyed"
            )
        return await self._service.unwrap(
            org_id,
            key.agent_session_id,
            key.version,
            WrappedKey(blob=key.wrapped, wrapping=key.wrapping),
        )
