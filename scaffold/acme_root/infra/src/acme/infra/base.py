"""What every infra module shares and nothing above infra provides: the
frozen model base, the frozen mapping field, the system scope, the two
helpers a twin needs, and the mark of a quiet null object. Infra imports nothing from the object model; the object
model imports infra, and takes its frozen mapping from here."""

from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Annotated, Any
from uuid import UUID, uuid7

from pydantic import AfterValidator, BaseModel, ConfigDict, PlainSerializer

SYSTEM_SCOPE = UUID(int=0)
"""The reserved system scope, by value: the object model's `EMPTY_UUID` is
the same UUID, and infra checks against it without importing the model."""


class QuietNull:
    """The mark of a quiet null object: an impl nobody provided that does
    nothing and answers a declared degraded answer, such as a gate that
    holds nothing or a signal that never signals. A loud null refuses with a
    typed error instead, and carries no mark. A root reads the mark to
    refuse a quiet budget gate or ledger outside `local`."""


def new_id() -> UUID:
    """A time-ordered UUID v7 as a standard-library UUID."""
    return uuid7()


def utcnow() -> datetime:
    return datetime.now(UTC)


class InfraModel(BaseModel):
    """Root of the values infra hands out. Frozen, strict on unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def _frozen(value: Any) -> Any:
    """A mapping becomes a read-only view of frozen values, a list a tuple of
    them, and anything else travels as it is."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _frozen(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_frozen(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    """The way back: plain dicts and lists, the containers JSON has."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


def freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _frozen(item) for key, item in value.items()})


def thaw_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _plain(item) for key, item in value.items()}


FrozenMapping = Annotated[
    Mapping[str, Any],
    AfterValidator(freeze_mapping),
    PlainSerializer(thaw_mapping, return_type=dict),
]
"""A mapping field that stays frozen past the model: pydantic validates a
`Mapping` into a dict, so a read-only view is put around it. The freeze
reaches what the mapping holds, because a proxy freezes only the mapping it
wraps and a payload of dumped JSON is nested: a nested mapping is wrapped the
same way and a nested list becomes a tuple. The serializer rebuilds plain
dicts and lists on the way out, so a stored payload is JSON again. The empty
case is `Field(default_factory=dict, validate_default=True)`, or the default
is the one dict that escapes the freeze."""
