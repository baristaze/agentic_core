"""Root of the object model: the base class, the mixins, the two helpers
every entity constructor needs, and the frozen mapping field, which infra's
base holds."""

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid7

from pydantic import BaseModel, ConfigDict

# The frozen mapping field is infra's, so a provider's values below the object
# model hold one too; every entity takes it from here.
from acme.infra.base import FrozenMapping as FrozenMapping
from acme.infra.base import freeze_mapping as freeze_mapping
from acme.infra.base import thaw_mapping as thaw_mapping


def new_id() -> UUID:
    """A time-ordered UUID v7 as a standard-library UUID."""
    return uuid7()


def derived_id(key: UUID, at: datetime, part: str = "") -> UUID:
    """The one id not minted fresh: a v7 whose time is `at` and whose random
    bits are taken from `key`, and from `part` when one key makes many (an
    import's rows). An entity an outside delivery creates takes it, with the
    delivery's key and the time the platform received it, so the same
    delivery handled twice presents the same id and the create meets the row
    already there; so does a row of an import stepped twice. It sorts by time
    like every other id."""
    millis = int(at.timestamp() * 1000) & ((1 << 48) - 1)
    digest = hashlib.sha256(key.bytes + part.encode()).digest()
    tail = int.from_bytes(digest[:10], "big")
    rand_a = tail >> 68  # 12 bits
    rand_b = tail & ((1 << 62) - 1)  # 62 bits
    value = (millis << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return UUID(int=value)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Platform(BaseModel):
    """Root of the object model. Holds no fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Identifiable(Platform):
    id: UUID  # uuid_v7, from new_id()


class Named(Platform):
    name: str


class Created(Platform):
    """When a row came to be, for a row the platform writes for itself, such
    as an outbox row, an idempotency record, or a socket ticket. No person
    stands behind it, so it carries no `created_by`, and what the platform
    stamps on it later is a field named for what happened (`done_at`,
    `redeemed_at`). An entity a person makes and changes composes
    `Trackable` instead."""

    created_at: datetime


class Trackable(Created):
    updated_at: datetime
    created_by: UUID  # id of the user who created it
    updated_by: UUID  # id of the user who last changed it


class SoftDeletable(Platform):
    deleted_at: datetime | None = None
    deleted_by: UUID | None = None


PROVENANCE_FIELDS = frozenset({"created_at", "created_by", "deleted_at", "deleted_by"})
"""Who made a row and who deleted it. The copy on update starts from the
stored row and takes the caller's fields with these excluded, so no caller
rewrites who made a row or brings a deleted one back by sending an entity.

Beside it, each entity that a caller updates by sending it declares
`MANAGER_OWNED_FIELDS`, a `ClassVar[tuple[str, ...]]`: the fields its manager
sets through the operations that own them and a caller never writes. The copy
on update excludes both sets."""


EMPTY_UUID = UUID(int=0)
"""The platform, not a tenant or a person: the reserved system scope, and the
value of a required reference that no tenant and no person owns. It equals
infra's `SYSTEM_SCOPE` by value, so infra never imports it. A reference that
is genuinely optional is `None`, never `EMPTY_UUID`."""
