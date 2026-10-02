"""Who stands behind a step. Three answers, each its own field: the actor
that produced it (on the step), the principal on whose authority it runs
(on an input and a tool call), and the spender who pays (on a model
request).

A principal is a user of the tenant: a person speaking for themselves or
through a program, or a service principal the tenant grants. An agent is
never one. Its steps name it as their actor, with its kind and its session
(`AgentRef`), so an audit answers "which agent" without the agent holding
a permission of its own."""

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from acme.om.base import Platform

MAX_KIND = 200
"""The longest agent kind's name a reference carries."""


class PrincipalKind(StrEnum):
    PERSON = "person"  # a user of the tenant, directly or through a program
    SERVICE = "service"  # a service principal the tenant grants


class Principal(Platform):
    """On whose authority something runs, or who pays for a model call: a
    user of the tenant by its id."""

    kind: PrincipalKind
    id: UUID


class AgentRef(Platform):
    """The agent behind a step whose actor is an agent: its kind, the
    kind's version, and the session it runs in. A reference, never a
    grant."""

    kind: str = Field(min_length=1, max_length=MAX_KIND)
    version: int = Field(ge=1)
    session_id: UUID
