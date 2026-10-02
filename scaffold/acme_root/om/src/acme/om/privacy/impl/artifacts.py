"""The seal an artifact's text takes on its way to the object store, under
its session's key, as a step's content is sealed on its way into storage.

The text is sealed under the current version of the session's key with
AES-GCM, bound to the tenant, the session, the artifact, and the version,
so a blob copied to another artifact opens nothing. The blob carries the
version it was sealed under, and opens with that version alone: once the
version is destroyed, the blob is noise and the artifact's record stays. A
session that keeps no content at rest keeps no artifact either."""

import os
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from acme.om.context import TenantContext
from acme.om.privacy.impl.sealed_steps import NONCE_BYTES
from acme.om.privacy.keys import SessionKeysInterface
from acme.om.privacy.storage import PrivacyStorageInterface
from acme.om.privacy.types.session_privacy import StorageMode
from acme.om.windows.seal import ArtifactSealInterface

VERSION_BYTES = 8
"""The version of the key a blob was sealed under, at its head."""


def bound_to(org_id: UUID, session_id: UUID, artifact_id: UUID, version: int) -> bytes:
    """What a sealed artifact is bound to: the tenant, the session, the
    artifact, and the version of the key."""
    return (
        b"artifact content"
        + org_id.bytes
        + session_id.bytes
        + artifact_id.bytes
        + version.to_bytes(VERSION_BYTES, "big")
    )


class ArtifactSealKeysImpl(ArtifactSealInterface):
    def __init__(self, keys: SessionKeysInterface, policies: PrivacyStorageInterface) -> None:
        self._keys = keys
        self._policies = policies

    async def seal(
        self, ctx: TenantContext, session_id: UUID, artifact_id: UUID, data: bytes
    ) -> bytes | None:
        record = await self._policies.read_privacy(ctx.org_id, session_id)
        if record is not None and record.policy.mode is StorageMode.MEMORY_ONLY:
            return None
        current = await self._keys.current(ctx.org_id, session_id)
        nonce = os.urandom(NONCE_BYTES)
        bound = bound_to(ctx.org_id, session_id, artifact_id, current.version)
        return (
            current.version.to_bytes(VERSION_BYTES, "big")
            + nonce
            + AESGCM(current.key).encrypt(nonce, data, bound)
        )

    async def open(
        self, ctx: TenantContext, session_id: UUID, artifact_id: UUID, sealed: bytes
    ) -> bytes | None:
        if len(sealed) <= VERSION_BYTES + NONCE_BYTES:
            raise ValueError(f"artifact {artifact_id} holds no sealed blob")
        version = int.from_bytes(sealed[:VERSION_BYTES], "big")
        keys = await self._keys.opened(ctx.org_id, session_id, {version})
        key = keys.get(version)
        if key is None:
            return None
        nonce = sealed[VERSION_BYTES : VERSION_BYTES + NONCE_BYTES]
        bound = bound_to(ctx.org_id, session_id, artifact_id, version)
        try:
            return AESGCM(key).decrypt(nonce, sealed[VERSION_BYTES + NONCE_BYTES :], bound)
        except InvalidTag:
            raise ValueError(
                f"artifact {artifact_id} does not open under version {version} of its key"
            ) from None
