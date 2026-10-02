"""Pure rules of tools: the hash a call is bound by, the policy's decision,
a call's time, a person's verdict, what recovery may repeat, and the
responses the model reads. Values in, values out; the time is an argument."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from acme.infra.transports import CommandResult
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.attribution.types.principal import AgentRef, Principal
from acme.om.base import thaw_mapping
from acme.om.context import Role
from acme.om.steps.types.content import Content, TextBlock, ToolResultBlock, ToolUseBlock
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    ToolFailure,
    ToolRequestHeader,
    ToolResponseHeader,
)
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.tools.types.call import Verdict
from acme.om.tools.types.mcp import McpToolDefinition
from acme.om.tools.types.policy import (
    DEFAULT_APPROVERS,
    Decision,
    PolicyCall,
    PolicyLayer,
    PolicyRule,
    ToolPolicy,
)
from acme.om.tools.types.tool import Effect, ToolClass

UNMATCHED = Decision.APPROVE
"""What a call no layer speaks to gets: a person decides."""

DEFAULT_CEILINGS = PolicyLayer(
    rules=(
        PolicyRule(authorization_class=ToolClass.DESTRUCTIVE, decision=Decision.APPROVE),
        PolicyRule(target={"outward": True}, decision=Decision.APPROVE),
    )
)
"""The platform's ceilings out of the box: what is destructive, and what
acts outward, waits for a person. An adopter adds its own, such as one for
each class it declares that must always wait for a person."""

ADVICE: dict[ToolFailure, str] = {
    ToolFailure.INVALID_INPUT: "The input does not fit the tool's schema. Correct it and call again.",
    ToolFailure.TRANSIENT: "This may pass on its own. Retrying is reasonable.",
    ToolFailure.TIMEOUT: "The call ran out of time. Narrow it, or split it, before you call again.",
    ToolFailure.DENIED: "This call is not allowed as asked. Change your plan; do not repeat it.",
    ToolFailure.INTERRUPTED: (
        "The call was stopped, and whether its effect happened is unknown. "
        "Check the state it would have changed before you call again."
    ),
    ToolFailure.PERMANENT: "This will not work as asked. Try another approach.",
}
"""What the model reads with each failure: the contract of a failure."""


# The call and its hash.


def input_hash(call_input: Mapping[str, Any]) -> str:
    """The hash an approval binds: SHA-256 over the input's canonical JSON,
    keys sorted, so the same input hashes the same however it was written."""
    plain = thaw_mapping(call_input)
    canonical = json.dumps(plain, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def definition_hash(definition: McpToolDefinition) -> str:
    """The pin of a server's tool: SHA-256 over its whole definition as the
    server lists it, annotations included, so any change to what the model
    or the adopter reads moves it."""
    canonical = json.dumps(
        definition.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def tool_request(
    step_id: UUID,
    at: datetime,
    response: Step,
    use: ToolUseBlock,
    authorization_class: str,
    *,
    principal: Principal,
    authority: AuthorityMode,
    agent: AgentRef,
) -> Step:
    """The request step of one tool use of a model response, the agent's act:
    it references the response and the use's id, carries the input's hash,
    never the input, and names the principal the call runs under."""
    return Step(
        id=step_id,
        created_at=at,
        session_id=response.session_id,
        loop_id=response.loop_id,
        type=StepType.TOOL_REQUEST,
        actor=Actor.AGENT,
        origin=Origin.ENGINE,
        refs=(response.id,),
        header=ToolRequestHeader(
            tool=use.name,
            tool_use_id=use.id,
            input_hash=input_hash(use.input),
            principal=principal,
            authority=authority,
            agent=agent,
            authorization_class=authorization_class,
        ),
    )


# Policy.


def matches(rule: PolicyRule, call: PolicyCall) -> bool:
    return (
        (rule.tool is None or rule.tool == call.tool)
        and (
            rule.authorization_class is None or rule.authorization_class == call.authorization_class
        )
        and (rule.effect is None or rule.effect is call.effect)
        and (rule.target_kind is None or rule.target_kind == call.target.kind)
        and all(
            name in call.target.attributes and call.target.attributes[name] == value
            for name, value in rule.target.items()
        )
    )


def specificity(rule: PolicyRule) -> int:
    selectors = (rule.tool, rule.authorization_class, rule.effect, rule.target_kind)
    return sum(selector is not None for selector in selectors) + len(rule.target)


def strictest(*decisions: Decision) -> Decision:
    return max(decisions, key=lambda decision: decision.strictness)


def decide(
    call: PolicyCall, defaults: PolicyLayer, tenant: PolicyLayer, ceilings: PolicyLayer
) -> Decision:
    """The most specific rule that matches, of the kind's defaults and the
    tenant's layer together, decides; where the two are equally specific
    the tenant's does, so a tenant narrows or loosens a default by naming
    the call as closely. Rules alike in both give their strictest. Then
    every ceiling that matches caps it: nothing below a ceiling loosens a
    call past it. A call no rule speaks to waits for a person."""
    found = [
        ((specificity(rule), layer), rule.decision)
        for layer, rules in enumerate((defaults.rules, tenant.rules))
        for rule in rules
        if matches(rule, call)
    ]
    chosen = UNMATCHED
    if found:
        top = max(rank for rank, _ in found)
        chosen = strictest(*(decision for rank, decision in found if rank == top))
    caps = [rule.decision for rule in ceilings.rules if matches(rule, call)]
    return strictest(chosen, *caps)


def approver_roles(policy: ToolPolicy, authorization_class: str) -> tuple[Role, ...]:
    for rule in policy.approvers:
        if rule.authorization_class == authorization_class:
            return rule.roles
    return DEFAULT_APPROVERS


# Time.


def call_deadline(
    now: datetime, timeout: timedelta, engine_limit: timedelta, tree_deadline: datetime | None
) -> datetime:
    """A call's time is the least of its tool's timeout, the engine's limit,
    and what is left before the tree's deadline."""
    deadline = now + min(timeout, engine_limit)
    return deadline if tree_deadline is None else min(deadline, tree_deadline)


def job_deadline(now: datetime, timeout: timedelta, tree_deadline: datetime | None) -> datetime:
    """A job carries its own deadline, never later than the tree's."""
    deadline = now + timeout
    return deadline if tree_deadline is None else min(deadline, tree_deadline)


# A person's decision.


def decides(control: Step, request: Step) -> bool:
    """Whether a control step is a person's decision on exactly this call:
    it references the request, and names its tool and its input's hash."""
    header = control.header
    call = request.header
    return (
        control.type is StepType.CONTROL
        and control.actor is Actor.PERSON
        and isinstance(header, ControlHeader)
        and header.call is not None
        and isinstance(call, ToolRequestHeader)
        and control.refs == (request.id,)
        and header.call.tool == call.tool
        and header.call.input_hash == call.input_hash
    )


def verdict(request: Step, later: Sequence[Step], now: datetime) -> tuple[Verdict, Step | None]:
    """A person's verdict on a call, from the steps after its request in
    order: the latest decision on exactly this call holds. An approval that
    has expired asks again; a denial holds for good."""
    latest = next((step for step in reversed(later) if decides(step, request)), None)
    header = None if latest is None else latest.header
    if not isinstance(header, ControlHeader) or header.call is None:
        return Verdict.PENDING, None
    if header.command is ControlCommand.DENY:
        return Verdict.DENIED, latest
    if header.call.expires_at is not None and header.call.expires_at <= now:
        return Verdict.EXPIRED, latest
    return Verdict.APPROVED, latest


# Failures.

STALE_STATUS = 412
"""The status the transport refuses a stale run's command with: the run lost
its claim, so it ends, and no response is written."""

CAPABILITY_MISSING = "capability_missing"
"""The code of a capability the agent does not have, such as a workspace."""

INFRA_FAILURES: dict[str, ToolFailure] = {"path_outside_workspace": ToolFailure.INVALID_INPUT}
"""A failure infra raised whose class its code decides: a path the model
chose that leads out of the workspace is an input it can correct."""


def infra_failure(http_status: int, code: str) -> ToolFailure:
    """The class of a failure infra raised, read off its status and its code:
    what cannot be reached right now is worth retrying, and the rest will
    not work as asked."""
    if code in INFRA_FAILURES:
        return INFRA_FAILURES[code]
    return ToolFailure.TRANSIENT if http_status == 503 else ToolFailure.PERMANENT


# Recovery and retries.


def engine_retries(failure: ToolFailure, effect: Effect) -> bool:
    """The engine retries on its own only a transient failure of a tool a
    repeat cannot harm; repeating an unsafe call is the model's decision."""
    return failure is ToolFailure.TRANSIENT and effect.repeatable


# Responses.


def bounded(text: str, limit: int) -> str:
    """Text the model reads, within its bound, saying what was cut."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n[cut: {limit} of {len(text)} characters shown]"


def response(
    step_id: UUID,
    at: datetime,
    request: Step,
    text: str,
    failure: ToolFailure | None = None,
    *,
    limit: int,
) -> Step:
    """The response step to a request: a result, or a failure with its class
    and the advice the model reads with it. The text is bounded to `limit`
    characters whatever it holds, a failure's included; the class and the
    advice are never cut."""
    header = request.header
    if not isinstance(header, ToolRequestHeader):
        raise ValueError(f"step {request.id} is not a tool request")
    text = bounded(text, limit)
    if failure is not None:
        text = f"{failure.value}: {text}\n{ADVICE[failure]}"
    return Step(
        id=step_id,
        created_at=at,
        session_id=request.session_id,
        loop_id=request.loop_id,
        type=StepType.TOOL_RESPONSE,
        actor=Actor.ENGINE,
        origin=Origin.ENGINE,
        responds_to=request.id,
        header=ToolResponseHeader(failure=failure),
        content=Content(
            blocks=(
                ToolResultBlock(
                    tool_use_id=header.tool_use_id,
                    parts=(TextBlock(text=text),),
                    is_error=failure is not None,
                ),
            )
        ),
    )


def command_text(result: CommandResult) -> str:
    """A command's end as the model reads it. A non-zero exit is a result,
    often the most useful one."""
    lines = [f"exit code: {result.exit_code}"]
    if result.stdout:
        lines += ["stdout:", result.stdout]
    if result.stderr:
        lines += ["stderr:", result.stderr]
    if result.truncated:
        lines.append("[the output was cut at its bound]")
    return "\n".join(lines)


def recovered_text(result: CommandResult) -> str:
    return (
        "The run that made this call was lost; the call's command had ended, "
        "and this is how, from the transport's record.\n" + command_text(result)
    )
