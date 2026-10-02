"""The tool contract: what every tool declares, whether it comes from native
code, from an MCP server, or from a sub-agent. The model sees the name, the
description, and the input schema. Policy keys on the class and the target;
the effect decides what may be repeated. A tool's input is a typed model
that refuses unknown fields, and its output is a typed model, rendered to
the model within a bound."""

from datetime import timedelta
from enum import StrEnum

from pydantic import Field

from acme.infra.transports import SecretUse
from acme.om.base import FrozenMapping, Platform

TOOL_NAME = r"^[a-z][a-z0-9_]{0,63}$"
CLASS_NAME = r"^[a-z][a-z0-9_]{0,63}$"


class Effect(StrEnum):
    """What a repeat of a call may do."""

    READ_ONLY = "read_only"  # reads
    IDEMPOTENT = "idempotent"  # safe to repeat, natively or under the idempotency key
    UNSAFE = "unsafe"  # a repeat may duplicate a side effect

    @property
    def repeatable(self) -> bool:
        return self is not Effect.UNSAFE


class ToolMode(StrEnum):
    SYNC = "sync"  # answers within the run
    JOB = "job"  # starts work that outlives the run, and answers when it completes


class ToolClass(StrEnum):
    """The authorization classes every engine knows. A product adds domain
    classes, such as acting on physical hardware, by declaring them to the
    registry; a class is a name either way, and policy keys on it."""

    READ = "read"  # reading the workspace, records, or artifacts
    WRITE = "write"  # changing files in the workspace
    EXECUTE = "execute"  # running code in the workspace; a build script runs anything
    NETWORK = "network"  # reaching an arbitrary external endpoint
    INTEGRATION = "integration"  # acting on a bound external system
    SPAWN = "spawn"  # starting sessions: sub-agents and handoffs
    CONFIGURATION = "configuration"  # changing project or platform configuration
    CREDENTIALS = "credentials"  # creating, rotating, or binding secret references
    DESTRUCTIVE = "destructive"  # irreversible changes outside the workspace


class ToolInput(Platform):
    """The root of every tool's input: frozen, and refusing a field it does
    not declare, so the schema the model reads is the one the call holds."""


class ToolSpec(Platform):
    """What a tool declares. `secrets` names what its process may be given,
    by name, never by value; a call that uses one is audited by its name."""

    name: str = Field(pattern=TOOL_NAME)
    description: str = Field(min_length=1)
    input_model: type[ToolInput]
    output_model: type[Platform]
    timeout: timedelta = Field(gt=timedelta(0))
    authorization_class: str = Field(pattern=CLASS_NAME)
    effect: Effect
    interruptible: bool
    mode: ToolMode = ToolMode.SYNC
    secrets: tuple[SecretUse, ...] = ()


class ToolDefinition(Platform):
    """A tool as the model reads it: the registry renders these in a fixed
    order, so the prompt prefix stays stable."""

    name: str
    description: str
    input_schema: FrozenMapping
