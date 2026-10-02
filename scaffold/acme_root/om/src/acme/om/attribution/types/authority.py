"""The authority a session's tool calls run under, and what a tool call
is answered with.

An agent kind picks its mode. Delegated: a call runs with the asking
person's live permissions, asked again of the adopter's transition on
every call. Steady: a call runs under one principal fixed when the
session is made, whoever resumes it; when that principal no longer holds,
the call waits until a person takes the session over."""

from enum import StrEnum
from typing import ClassVar

from pydantic import Field

from acme.om.attribution.types.principal import Principal
from acme.om.base import Identifiable, Platform, Trackable
from acme.om.context import TenantContext


class AuthorityMode(StrEnum):
    DELEGATED = "delegated"  # the asking person's live permissions, asked on every call
    STEADY = "steady"  # one principal fixed when the session is made


class Trust(StrEnum):
    """What a step says to a model. Only a principal instructs; everything
    else is data, rendered quoted and labelled with its origin."""

    INSTRUCTION = "instruction"
    DATA = "data"


class SessionAuthority(Identifiable, Trackable):
    """A session's authority, one record keyed by the session's id. Made
    once, right after the session, and never by a caller's say: a root runs
    under the person who made it; a session made from another runs under
    the principal that session's calls run under, and a child pays as its
    parent pays (`attribution.rules.inherited`).

    `principal` is the one a steady session's calls run under, and a
    child's in either mode. A delegated session's calls run under the
    speaker the request that led to them recorded, and under `principal`
    until a principal speaks. `spender` pays until a principal speaks in
    the session: what a child's spawn passed it, or nobody."""

    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = ("principal", "spender", "version")
    """Inherited, or taken over; the caller names the mode alone."""

    mode: AuthorityMode
    principal: Principal
    spender: Principal | None = None
    # Every write after the create is a compare-and-set on it.
    version: int = Field(default=1, ge=1)


class RequestAttribution(Platform):
    """What a model request records: the speaker, the principal behind the
    latest principal-authored input the model has received, its delivery
    included, and the spender who pays for it."""

    speaker: Principal | None
    spender: Principal


class CallReach(Platform):
    """What a tool call's policy knows of it and of the session that asks
    it: whether the call acts outward (on external state beyond the
    session's own work product, or past its egress allowlist), and whether
    the session holds private data or credentials."""

    outward: bool
    holds_private: bool


class CallAuthority(Platform):
    """A tool call's answer: the principal it runs under, the mode that
    chose it, that principal's live context as the adopter's transition
    gave it this time, and whether a person must approve it (the rule of
    two)."""

    principal: Principal
    mode: AuthorityMode
    context: TenantContext
    needs_person: bool
