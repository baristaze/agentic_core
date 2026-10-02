"""The authority a session's tool calls run under, and what a tool call
is answered with.

An agent kind picks its mode. Delegated: a call runs with the asking
person's live permissions, asked again of the adopter's transition on
every call. Steady: a call runs under one principal fixed when the
session is made, whoever resumes it; when that principal no longer holds,
the call parks until a person takes it over."""

from enum import StrEnum

from acme.om.attribution.types.principal import Principal
from acme.om.base import Platform
from acme.om.context import TenantContext


class AuthorityMode(StrEnum):
    DELEGATED = "delegated"  # the asking person's live permissions, asked on every call
    STEADY = "steady"  # one principal fixed when the session is made


class Trust(StrEnum):
    """What a step says to a model. Only a principal instructs; everything
    else is data, rendered quoted and labelled with its origin."""

    INSTRUCTION = "instruction"
    DATA = "data"


class Authority(Platform):
    """A session's authority. `principal` is the one a steady session's
    calls run under. A delegated session's calls run under whoever asked
    last, and under `principal` until a principal speaks: the person who
    made it, or for a child the principal its spawn ran under."""

    mode: AuthorityMode
    principal: Principal


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
