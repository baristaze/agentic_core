import base64
import binascii
import os
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from acme.infra.keys import DATA_KEY_BYTES, DataKey, KeyRefused, KeyServiceInterface, WrappedKey

NONCE_BYTES = 12
VERSION_BYTES = 4


def bound_name(org_id: UUID, key_id: UUID, version: int) -> bytes:
    """What a wrapped key is bound to: the tenant, the key, and its version.
    Presented under any other name, the tag does not verify."""
    return b"data key" + org_id.bytes + key_id.bytes + version.to_bytes(8, "big")


def root_key(encoded: str) -> bytes:
    """The 32 bytes a root key setting encodes, as URL-safe base64; any
    other shape refuses the boot."""
    try:
        decoded = base64.urlsafe_b64decode(encoded.encode())
    except (binascii.Error, ValueError):
        decoded = b""
    if len(decoded) != DATA_KEY_BYTES:
        raise ValueError(f"the keys root key is URL-safe base64 of {DATA_KEY_BYTES} bytes")
    return decoded


class KeyServiceMemoryImpl(KeyServiceInterface):
    """The twin. Each tenant's wrapping key lives in this process, derived
    from one root key and the tenant's id, a version at a time, so a process
    that restarts with the same root key unwraps what it wrapped. With no
    root key it draws one at random, and what it wrapped dies with it: a
    test's, or a process that keeps nothing at rest.

    `rotate` moves a tenant's wrapping key one version up, in this process,
    standing in for the cloud key service's own rotation."""

    def __init__(self, root_key: bytes | None = None) -> None:
        if root_key is not None and len(root_key) != DATA_KEY_BYTES:
            raise ValueError(f"the root key is {DATA_KEY_BYTES} bytes")
        self._root = root_key if root_key is not None else os.urandom(DATA_KEY_BYTES)
        self._random = root_key is None
        self._current: dict[UUID, int] = {}

    def rotate(self, org_id: UUID) -> int:
        """The tenant's wrapping key, one version up; the version it is now."""
        self._current[org_id] = self._current.get(org_id, 1) + 1
        return self._current[org_id]

    def _wrapping_key(self, org_id: UUID, wrapping: int) -> AESGCM:
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=DATA_KEY_BYTES,
            salt=None,
            info=b"wrapping key" + org_id.bytes + wrapping.to_bytes(VERSION_BYTES, "big"),
        ).derive(self._root)
        return AESGCM(derived)

    def _wrap(self, org_id: UUID, key_id: UUID, version: int, plaintext: bytes) -> WrappedKey:
        wrapping = self._current.get(org_id, 1)
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._wrapping_key(org_id, wrapping).encrypt(
            nonce, plaintext, bound_name(org_id, key_id, version)
        )
        return WrappedKey(
            blob=wrapping.to_bytes(VERSION_BYTES, "big") + nonce + sealed,
            wrapping=f"memory:{wrapping}",
        )

    async def generate(self, org_id: UUID, key_id: UUID, version: int) -> DataKey:
        plaintext = os.urandom(DATA_KEY_BYTES)
        return DataKey(plaintext=plaintext, wrapped=self._wrap(org_id, key_id, version, plaintext))

    async def unwrap(
        self, org_id: UUID, key_id: UUID, version: int, wrapped: WrappedKey
    ) -> bytes:
        blob = wrapped.blob
        head = VERSION_BYTES + NONCE_BYTES
        if len(blob) <= head:
            raise KeyRefused(f"version {version} of key {key_id} is not a wrapped key")
        wrapping = int.from_bytes(blob[:VERSION_BYTES], "big")
        try:
            return self._wrapping_key(org_id, wrapping).decrypt(
                blob[VERSION_BYTES:head], blob[head:], bound_name(org_id, key_id, version)
            )
        except InvalidTag:
            raise KeyRefused(f"version {version} of key {key_id} does not unwrap here") from None

    async def rewrap(
        self, org_id: UUID, key_id: UUID, version: int, wrapped: WrappedKey
    ) -> WrappedKey:
        plaintext = await self.unwrap(org_id, key_id, version, wrapped)
        return self._wrap(org_id, key_id, version, plaintext)

    def describe(self) -> str:
        return "keys=memory(random root)" if self._random else "keys=memory(root from settings)"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
