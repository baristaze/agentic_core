"""Policy: one of three decisions for a call, from the call's tool, its
class, its effect, and the attributes of what it targets. Never from what
the model says about the call: nothing here has a field for it.

Three layers decide. The agent kind's defaults first; the tenant's layer
narrows or loosens them; the platform's ceilings cap the result, so no
tenant loosens a call past them. The tenant's layer is stored, with who may
approve each class."""

from enum import StrEnum
from typing import ClassVar, Self

from pydantic import Field, model_validator

from acme.om.base import FrozenMapping, Identifiable, Platform, Trackable
from acme.om.context import Role
from acme.om.tools.types.tool import CLASS_NAME, TOOL_NAME, Effect


class Decision(StrEnum):
    """From the least strict to the most."""

    ALLOW = "allow"  # runs unattended
    APPROVE = "approve"  # waits for a person's approval
    DENY = "deny"  # never runs

    @property
    def strictness(self) -> int:
        return STRICTNESS[self]


STRICTNESS = {Decision.ALLOW: 0, Decision.APPROVE: 1, Decision.DENY: 2}


class Target(Platform):
    """What a call acts on, as the system it acts on reports it (a branch
    and whether it is protected, a station and its kind), never as the
    call's input describes it. A call that targets nothing in particular
    has an empty one."""

    kind: str | None = None
    attributes: FrozenMapping = Field(default_factory=dict, validate_default=True)


class PolicyCall(Platform):
    """Everything policy reads of a call, and all of it: the model's text and
    the call's input are not among it."""

    tool: str
    authorization_class: str
    effect: Effect
    target: Target = Field(default_factory=Target)


class PolicyRule(Platform):
    """One row of a layer: what it matches and what it decides. A selector
    left unset matches any call; every target attribute it names must equal
    the target's. The more selectors a rule sets, the more specific it is."""

    tool: str | None = Field(default=None, pattern=TOOL_NAME)
    authorization_class: str | None = Field(default=None, pattern=CLASS_NAME)
    effect: Effect | None = None
    target_kind: str | None = None
    target: FrozenMapping = Field(default_factory=dict, validate_default=True)
    decision: Decision


class PolicyLayer(Platform):
    rules: tuple[PolicyRule, ...] = ()


class ApproverRule(Platform):
    """Who may approve a call of one class in the tenant. A class with no
    rule takes the owners and the admins. A service is never an approver:
    an approval is a person's decision."""

    authorization_class: str = Field(pattern=CLASS_NAME)
    roles: tuple[Role, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _people_alone(self) -> Self:
        if Role.SERVICE in self.roles:
            raise ValueError("a service never approves a call")
        return self


DEFAULT_APPROVERS: tuple[Role, ...] = (Role.OWNER, Role.ADMIN)


class ToolPolicy(Identifiable, Trackable):
    """The tenant's layer of policy, one per tenant, and who approves what."""

    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = ("version",)

    rules: tuple[PolicyRule, ...] = ()
    approvers: tuple[ApproverRule, ...] = ()
    # Every write after the create is a compare-and-set on it.
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _one_rule_a_class(self) -> Self:
        classes = [rule.authorization_class for rule in self.approvers]
        if len(set(classes)) != len(classes):
            raise ValueError("each class has one approver rule")
        return self

    def layer(self) -> PolicyLayer:
        return PolicyLayer(rules=self.rules)
