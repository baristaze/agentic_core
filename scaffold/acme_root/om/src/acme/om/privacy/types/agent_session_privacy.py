"""What the privacy namespace holds of a session: the storage policy chosen
for it once, the versions of its key, and whether that key is revoked.

A session's content is sealed under its own key, by envelope: a data key
per version, kept here wrapped by the tenant's key service, never in the
clear. Revoking the key destroys every version's wrapped copy and keeps the
row that says the version existed, so the record of what was destroyed, and
when, outlives what it destroyed.

A session's title is sealed under a key of its own, the title key, kept on
its record wrapped under the tenant's title key, which the key service wraps.
So a page of titles opens with one call to the key service, and revoking the
session's key takes its title key in the same commit (ADR 1042)."""

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from acme.om.base import Created, Identifiable, OmBase


class StorageMode(StrEnum):
    """Where a session's content lives."""

    SEALED = "sealed"  # at rest, sealed under the session's key: the default
    MEMORY_ONLY = "memory_only"  # only while a runtime holds the session


class StoragePolicy(OmBase):
    """A session's storage, chosen once. A memory-only session keeps its
    shape at rest when `keep_shape` allows, so its audit survives; a sealed
    one always does. A call's cost survives in every mode, in its usage
    record (ADR 1014)."""

    mode: StorageMode = StorageMode.SEALED
    keep_shape: bool = True

    @model_validator(mode="after")
    def _sealed_keeps_its_shape(self) -> Self:
        if self.mode is StorageMode.SEALED and not self.keep_shape:
            raise ValueError("a sealed session keeps its shape")
        return self


class AgentSessionPrivacy(Identifiable, Created):
    """One per session of a tenant: its policy, chosen once before its
    history begins, the session's title key, and the revocation of its key.
    A session with none is sealed. Its title writes the record with the
    default, not yet chosen; the first step appended to it fixes the
    default."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64"
    )

    agent_session_id: UUID
    policy: StoragePolicy = StoragePolicy()
    # False while the policy is the default the session's title wrote, and
    # none is chosen yet: a choice, or the history's first write, fixes it.
    chosen: bool = True
    # The key the session's title is sealed under, wrapped under its tenant's
    # title key: written with the title, and gone in the commit that revokes
    # the session's key (ADR 1042).
    title_key: bytes | None = Field(default=None, repr=False)
    revoked_at: datetime | None = None
    revoked_by: UUID | None = None

    @model_validator(mode="after")
    def _revoked_holds_no_title_key(self) -> Self:
        if self.revoked_at is not None and self.title_key is not None:
            raise ValueError("a revoked session holds no title key")
        return self


class TenantTitleKey(Identifiable, Created):
    """One per tenant: the key each of its sessions' title keys is wrapped
    under, a data key the tenant's key service wraps, never kept in the
    clear. A page of titles opens with one unwrap of it, whatever its size
    (ADR 1042). A rotation wraps it again; it goes with the tenant."""

    wrapped: bytes = Field(repr=False)
    wrapping: str  # the wrapping key, as the key service names it
    wrapped_at: datetime


class TitleKeys(OmBase):
    """What one read finds for a page of titles: the tenant's title key, and
    the wrapped title key of each session that holds one."""

    tenant: TenantTitleKey | None = None
    sessions: dict[UUID, bytes] = Field(default_factory=dict, repr=False)


class AgentSessionKey(Identifiable, Created):
    """One version of a session's key, wrapped by the tenant's key service.
    Destroyed, it holds no wrapped copy, and the row stays."""

    agent_session_id: UUID
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


class KeyRing(OmBase):
    """A session's key as storage holds it: every version, live and
    destroyed, in version order, and whether the key is revoked."""

    revoked: bool = False
    keys: tuple[AgentSessionKey, ...] = ()

    def current(self) -> AgentSessionKey | None:
        """The version new content is sealed under: the highest, unless the
        key is revoked."""
        if self.revoked or not self.keys:
            return None
        return self.keys[-1]

    def version(self, version: int) -> AgentSessionKey | None:
        return next((key for key in self.keys if key.version == version), None)
