"""A blob sealed under one version of a session's key, the form an artifact's
text and a command's record both take: the version at its head, then the
nonce, then AES-GCM over the data, bound to what its caller names with the
version, so a blob copied anywhere else opens nothing. It opens with the
version it names alone: once that version is destroyed, it is noise."""

import os
from collections.abc import Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from acme.om.privacy.impl.sealed_steps import NONCE_BYTES
from acme.om.privacy.keys import OpenKey

VERSION_BYTES = 8
"""The version of the key a blob was sealed under, at its head."""

Bound = Callable[[int], bytes]
"""What a blob is bound to, given the version of the key it is sealed under."""


def seal_blob(key: OpenKey, data: bytes, bound: Bound) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    head = key.version.to_bytes(VERSION_BYTES, "big") + nonce
    return head + AESGCM(key.key).encrypt(nonce, data, bound(key.version))


def blob_version(sealed: bytes, what: str) -> int:
    """The version a blob names; `ValueError` when `sealed` holds no blob."""
    if len(sealed) <= VERSION_BYTES + NONCE_BYTES:
        raise ValueError(f"{what} holds no sealed blob")
    return int.from_bytes(sealed[:VERSION_BYTES], "big")


def open_blob(key: bytes, sealed: bytes, bound: Bound, what: str) -> bytes:
    """The data of a blob sealed under `key`, the version it names. A blob
    that does not open was sealed by no seal of this platform, or for
    something else, and is refused (`ValueError`), never read as anything."""
    version = blob_version(sealed, what)
    nonce = sealed[VERSION_BYTES : VERSION_BYTES + NONCE_BYTES]
    try:
        return AESGCM(key).decrypt(nonce, sealed[VERSION_BYTES + NONCE_BYTES :], bound(version))
    except InvalidTag:
        raise ValueError(f"{what} does not open under version {version} of its key") from None
