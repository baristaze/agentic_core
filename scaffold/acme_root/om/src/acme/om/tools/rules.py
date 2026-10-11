"""Pure rules of tools: the hash a call is bound by, the policy's decision,
a call's time, a person's verdict, what recovery may repeat, and the
responses the model reads. Values in, values out; the time is an argument."""

import hashlib
import json
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from acme.infra.transports import CommandResult
from acme.infra.workspaces import EgressMode
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.attribution.types.principal import AgentRef, Principal
from acme.om.base import derived_id, thaw_mapping
from acme.om.context import Permission, Role, TenantContext
from acme.om.steps.types.content import Content, TextBlock, ToolResultBlock, ToolUseBlock
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    SnapshotHeader,
    ToolFailure,
    ToolRequestHeader,
    ToolResponseHeader,
    WorkspaceSnapshot,
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
    Target,
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

INWARD_CLASSES = frozenset({ToolClass.READ, ToolClass.WRITE, ToolClass.EXECUTE, ToolClass.SPAWN})
"""The classes whose calls stay inside the session's own work by their
nature: its workspace, its records, its children. Every other class, a
domain class included, acts outward unless its target says it does not."""

CLASS_PERMISSIONS: dict[str, Permission] = {
    ToolClass.READ: Permission.READ,
    ToolClass.CONFIGURATION: Permission.MANAGE_MEMBERS,
    ToolClass.CREDENTIALS: Permission.MANAGE_KEYS,
}
"""The tenant permission a principal holds to make a call of a class (ADR
1012): reading needs `read`; changing configuration needs what changing
the tenant's own does, as its tool policy is written; binding secret
references needs what managing its keys does. Every other class, a domain
class included, needs `write`."""

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


# Who may start or instruct a session.


def permissions_for(classes: Iterable[str]) -> frozenset[Permission]:
    """What a principal holds to make every kind of call `classes` names."""
    return frozenset(CLASS_PERMISSIONS.get(name, Permission.WRITE) for name in classes)


def call_refusal(ctx: TenantContext, authorization_class: str) -> str | None:
    """Why the principal whose live context is `ctx` may not make a call of
    `authorization_class`, or None when it may. Asked at every call, so a
    permission taken away between two calls stops the second (ADR 1007)."""
    needed = CLASS_PERMISSIONS.get(authorization_class, Permission.WRITE)
    if ctx.has(needed):
        return None
    return f"{ctx.role.value} lacks {needed.value}, which a {authorization_class} call needs"


def instruct_refusal(ctx: TenantContext, classes: Iterable[str]) -> str | None:
    """Why `ctx` may not start or instruct a session whose registry offers
    `classes`, or None when it may: a message enqueues the session's loop,
    so its sender holds every permission a call the registry offers needs
    (the guideline's enqueue rule), and a message buys no call its sender
    may not make."""
    missing = sorted(p.value for p in permissions_for(classes) if not ctx.has(p))
    if not missing:
        return None
    return f"{ctx.role.value} lacks {', '.join(missing)}, which a tool of this session needs"


# The call and its hash.


INPUT_HASH = "hmac-sha256:"
"""The scheme of an input's hash: keyed by its session, so once the session's
key is revoked the hash confirms nothing about the input
(`ToolsManagerInterface.input_hash`)."""


def canonical_input(call_input: Mapping[str, Any]) -> bytes:
    """An input as its hash reads it: canonical JSON, keys sorted, so the same
    input reads the same however it was written."""
    plain = thaw_mapping(call_input)
    canonical = json.dumps(plain, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return canonical.encode()


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
    input_hash: str,
    principal: Principal,
    authority: AuthorityMode,
    agent: AgentRef,
) -> Step:
    """The request step of one tool use of a model response, the agent's act:
    it references the response and the use's id, carries the input's hash
    keyed by its session, never the input, and names the principal the call
    runs under."""
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
            input_hash=input_hash,
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


def reaches_outward(call: PolicyCall, egress: EgressMode) -> bool:
    """Whether a call acts outward, for the rule of two: on external state
    beyond the session's own work product, or past its egress allowlist.
    The target's `outward` attribute answers, as the system it acts on
    reports it. A target that does not say takes its class's answer, and a
    call that runs code acts outward from a workspace whose egress is open,
    since nothing there holds it to an allowlist."""
    said = call.target.attributes.get("outward")
    if isinstance(said, bool):
        return said
    if call.authorization_class == ToolClass.EXECUTE and egress is EgressMode.OPEN:
        return True
    return call.authorization_class not in INWARD_CLASSES


def with_reach(call: PolicyCall) -> PolicyCall:
    """The call as policy reads it: its target says whether it acts outward,
    in the target's own word or, where the target does not say, by its class
    alone. So the platform's outward ceiling, and any rule keyed on
    `outward`, meets a call of an outward class whose target does not mark
    it. A call that runs code in a workspace whose egress is open is not
    stamped outward here: that is a leg of the rule of two, which
    `reaches_outward` answers from the call as its target gave it, and a
    session that lacks the other leg runs it under its class's policy."""
    said = call.target.attributes.get("outward")
    outward = said if isinstance(said, bool) else call.authorization_class not in INWARD_CLASSES
    attributes = {**call.target.attributes, "outward": outward}
    return call.model_copy(update={"target": Target(kind=call.target.kind, attributes=attributes)})


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


def decides(control: Step, request: Step, approvers: Sequence[Role]) -> bool:
    """Whether a control step is a person's decision on exactly this call:
    it references the request, names its tool and its input's hash, and
    was made in a role among `approvers`, those the tenant's policy lets
    decide the call's class now. A decision in any other role is no
    decision, however it reached the history."""
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
        and header.call.role in approvers
    )


def verdict(
    request: Step, later: Sequence[Step], now: datetime, approvers: Sequence[Role]
) -> tuple[Verdict, Step | None]:
    """A person's verdict on a call, from the steps after its request in
    order: the latest decision on exactly this call, by a role among
    `approvers`, holds. An approval that has expired asks again; a denial
    holds for good."""
    latest = next((step for step in reversed(later) if decides(step, request, approvers)), None)
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

ISOLATION_REFUSED = "isolation_refused"
"""The code of a workspace provider's refusal of a spec it cannot meet."""

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
    """Text the model reads, within its bound: its head and its tail, where
    an output's end, its summary or the error that stopped it, usually is,
    with a line between them saying what was cut."""
    if len(text) <= limit:
        return text
    tail = limit // 2
    shown = f"[cut: {limit} of {len(text)} characters shown, the head above and the tail below]"
    return f"{text[: limit - tail]}\n{shown}\n{text[len(text) - tail :]}"


def shares(sizes: Sequence[int], room: int) -> list[int]:
    """What each of `sizes` may take of `room`: even shares, and a size
    within its share takes only itself and leaves the rest to the others."""
    allowed = [0] * len(sizes)
    left, count = max(room, 0), len(sizes)
    for index in sorted(range(len(sizes)), key=lambda at: sizes[at]):
        allowed[index] = min(sizes[index], left // count)
        left, count = left - allowed[index], count - 1
    return allowed


def fit(texts: Sequence[str], room: int, size: Callable[[str], int] = len) -> list[str]:
    """Texts the model reads together within `room`, as `size` measures
    each where it is read: one within its share whole, one past it cut by
    `bounded` until it fits, so each keeps its own head and tail whatever
    the others hold. A command's streams are such texts."""
    sizes = [size(text) for text in texts]
    if sum(sizes) <= room:
        return list(texts)
    fitted: list[str] = []
    for text, have, share in zip(texts, sizes, shares(sizes, room), strict=True):
        keep = max(0, share * len(text) // max(have, 1))
        cut = text if have <= share else bounded(text, keep)
        while size(cut) > share and keep > 0:
            keep = max(0, keep - (size(cut) - share))
            cut = bounded(text, keep)
        fitted.append(cut)
    return fitted


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list | tuple):
        return [text for item in value for text in _strings(item)]
    return []


def _put(value: Any, texts: Iterator[str]) -> Any:
    if isinstance(value, str):
        return next(texts)
    if isinstance(value, Mapping):
        return {key: _put(item, texts) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_put(item, texts) for item in value]
    return value


def bounded_json(value: Any, limit: int) -> str:
    """A tool's output as the model reads it, as JSON within `limit`
    characters where it can be: each string, such as a command's stdout and
    its stderr, cut to its share (`fit`), never the whole as one text, which
    would lose the end of every string but the last."""
    whole = _json(value)
    if len(whole) <= limit:
        return whole
    texts = _strings(value)
    measured = [len(_json(text)) for text in texts]
    room = limit - (len(whole) - sum(measured))
    return _json(_put(value, iter(fit(texts, room, size=lambda text: len(_json(text))))))


def response_id(request: Step, epoch: int) -> UUID:
    """The id of the response a run writes for a request, derived from the
    request and the run's writer epoch: the parts a call's output streams
    carry it before the response is written, and no two runs share it, so a
    run that lost its claim never lands its answer as the one a later run
    wrote: its append is refused."""
    return derived_id(request.id, request.created_at, f"tool_response:{epoch}")


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


def _command_lines(result: CommandResult, streams: Sequence[tuple[str, str]]) -> str:
    lines = [f"exit code: {result.exit_code}"]
    for name, text in streams:
        lines += [f"{name}:", text]
    if result.truncated:
        lines.append("[the output was cut at its bound]")
    return "\n".join(lines)


def command_text(result: CommandResult, limit: int) -> str:
    """A command's end as the model reads it, within `limit` characters:
    its stdout and its stderr each cut to its share (`fit`), so each keeps
    its own end. A non-zero exit is a result, often the most useful one."""
    streams = [(n, t) for n, t in (("stdout", result.stdout), ("stderr", result.stderr)) if t]
    frame = len(_command_lines(result, [(name, "") for name, _ in streams]))
    kept = fit([text for _, text in streams], limit - frame)
    return _command_lines(result, [(name, k) for (name, _), k in zip(streams, kept, strict=True)])


RECOVERED = (
    "The run that made this call was lost; the call's command had ended, "
    "and this is how, from the transport's record.\n"
)


def recovered_text(result: CommandResult, limit: int) -> str:
    return RECOVERED + command_text(result, limit - len(RECOVERED))


# Workspace snapshots.


def named_snapshot(step: Step) -> WorkspaceSnapshot | None:
    """The snapshot a step names: the one a `snapshotted` step says the
    session took, or the one a restore starts its workspace from."""
    header = step.header
    if isinstance(header, SnapshotHeader):
        return header.snapshot
    if isinstance(header, ControlHeader):
        return header.snapshot
    return None


def snapshotted_step(
    step_id: UUID, at: datetime, session_id: UUID, loop_id: UUID, snapshot: WorkspaceSnapshot
) -> Step:
    """The step that names a snapshot the session took of its workspace."""
    return Step(
        id=step_id,
        created_at=at,
        session_id=session_id,
        loop_id=loop_id,
        type=StepType.SNAPSHOTTED,
        actor=Actor.ENGINE,
        origin=Origin.ENGINE,
        header=SnapshotHeader(snapshot=snapshot),
    )
