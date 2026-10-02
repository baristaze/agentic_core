"""What the privacy namespace holds of a session: the storage policy chosen
for it once, the versions of its key, and whether that key is revoked.

A session's content is sealed under its own key, by envelope: a data key
per version, kept here wrapped by the tenant's key service, never in the
clear. Revoking the key destroys every version's wrapped copy and keeps the
row that says the version existed, so the record of what was destroyed, and
when, outlives what it destroyed."""

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from acme.om.base import Created, Identifiable, Platform


class StorageMode(StrEnum):
    """Where a session's content lives."""

    SEALED = "sealed"  # at rest, sealed under the session's key: the default
    MEMORY_ONLY = "memory_only"  # only while a runtime holds the session


class StoragePolicy(Platform):
    """A session's storage, chosen once. A memory-only session keeps its
    shape at rest when `keep_shape` allows, so its cost and its audit
    survive; a sealed one always does."""

    mode: StorageMode = StorageMode.SEALED
    keep_shape: bool = True

    @model_validator(mode="after")
    def _sealed_keeps_its_shape(self) -> Self:
        if self.mode is StorageMode.SEALED and not self.keep_shape:
            raise ValueError("a sealed session keeps its shape")
        return self


class SessionPrivacy(Identifiable, Created):
    """One per session of a tenant: its policy, written once and never
    changed, and the revocation of its key. A session with none is sealed;
    the first step appended to it writes the default."""

    session_id: UUID
    policy: StoragePolicy = StoragePolicy()
    revoked_at: datetime | None = None
    revoked_by: UUID | None = None


class SessionKey(Identifiable, Created):
    """One version of a session's key, wrapped by the tenant's key service.
    Destroyed, it holds no wrapped copy, and the row stays."""

    session_id: UUID
    version: int = Field(ge=1)
    wrapped: bytes | None = Field(default=None, repr=False)
    wrapping: str | None = None  # the wrapping key, as the key service names it
    wrapped_at: datetime | None = None
    destroyed_at: datetime | None = None

    @model_validator(mode="after")
    def _wrapped_or_destroyed(self) -> Self:
        live = self.wrapped is not None and self.wrapping is not None
        if live == (self.destroyed_at is not None):
            raise ValueError("a version holds its wrapped copy until it is destroyed")
        if live != (self.wrapped_at is not None):
            raise ValueError("a wrapped copy carries when it was wrapped")
        return self

    def is_destroyed(self) -> bool:
        return self.destroyed_at is not None


class KeyRing(Platform):
    """A session's key as storage holds it: every version, live and
    destroyed, in version order, and whether the key is revoked."""

    revoked: bool = False
    keys: tuple[SessionKey, ...] = ()

    def current(self) -> SessionKey | None:
        """The version new content is sealed under: the highest, unless the
        key is revoked."""
        if self.revoked or not self.keys:
            return None
        return self.keys[-1]

    def version(self, version: int) -> SessionKey | None:
        return next((key for key in self.keys if key.version == version), None)
