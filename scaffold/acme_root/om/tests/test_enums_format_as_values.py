"""Every enum the platform formats into a key, a name, a log line, or a
message formats as its value. A `(str, Enum)` member formats as `Class.MEMBER`
on Python 3.12 and later, so an f-string built from one names the class
instead of the value; every one of them is a `StrEnum`."""

import enum

import pytest

from acme.infra.buckets import Buckets
from acme.infra.cache import CacheScope
from acme.infra.queues import Queues
from acme.infra.topics import Topics
from acme.infra.transports import SecretVia
from acme.infra.workspaces import EgressMode, IsolationMode
from acme.integrations.model_providers.types import (
    Effort,
    ErrorAnswer,
    ErrorKind,
    ProviderName,
    StopReason,
)
from acme.om.agent_sessions.types.agent_session import SessionStatus
from acme.om.context import (
    AppType,
    CredentialKind,
    OperatorPermission,
    OperatorRole,
    Permission,
    Role,
)
from acme.om.media.types.file import FilePurpose, FileStatus
from acme.om.models.types.fill import OutputShape, SwitchReason
from acme.om.orchestrations.types.orchestration import OrchestrationKind, OrchestrationStatus
from acme.om.steps.types.header import ControlCommand, LoopOutcome, ParkReason, ToolFailure
from acme.om.steps.types.step import Actor, Origin, StepFamily, StepType
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.scopes import ScopeKind
from acme.om.tools.types.call import GateOutcome, Verdict
from acme.om.tools.types.policy import Decision
from acme.om.tools.types.tool import Effect, ToolClass, ToolMode
from acme.om.work.types.work_item import WorkKind, WorkStatus

ENUMS = [
    Buckets,
    CacheScope,
    Queues,
    Topics,
    AppType,
    CredentialKind,
    OperatorPermission,
    OperatorRole,
    Permission,
    Role,
    DatabaseRole,
    ScopeKind,
    FilePurpose,
    FileStatus,
    OrchestrationKind,
    OrchestrationStatus,
    WorkKind,
    WorkStatus,
    SessionStatus,
    StepType,
    StepFamily,
    Actor,
    Origin,
    ControlCommand,
    LoopOutcome,
    ParkReason,
    ProviderName,
    Effort,
    StopReason,
    ErrorKind,
    ErrorAnswer,
    OutputShape,
    SwitchReason,
    ToolFailure,
    Effect,
    ToolMode,
    ToolClass,
    Decision,
    Verdict,
    GateOutcome,
    IsolationMode,
    EgressMode,
    SecretVia,
]


@pytest.mark.parametrize("kind", ENUMS, ids=lambda kind: kind.__name__)
def test_a_member_formats_as_its_value(kind: type[enum.Enum]) -> None:
    assert issubclass(kind, enum.StrEnum)
    for member in kind:
        assert f"{member}" == member.value
        assert str(member) == member.value
