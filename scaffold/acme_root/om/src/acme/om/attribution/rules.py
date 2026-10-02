"""Pure rules of attribution: which input a principal wrote, which content
instructs and which is data, who pays for the next model call, whose
authority a tool call runs under, and when a convinced model needs a
person. Values in, values out; no clock, no storage.

A model request records its speaker: the principal behind the latest
principal-authored input the model has received, that request's included.
The calls a response leads to run under it, so a message that lands after
the request lends nobody's authority to them. The session caches two
answers its steps give, folded in `seq` order with its status: the speaker
of its latest model request, and the untrusted mark. A reader that needs
them now folds the steps after the cache's last `seq` with `fold`.

The mark is set when the first data lands, no later than the model reads
it: the next model request delivers every input that waits, and a tool's
response is read by the request after it. So no tool call is ever decided
on a mark the model's reading has outrun."""

from collections.abc import Iterable, Sequence
from uuid import UUID

from acme.om.attribution.types.authority import (
    AuthorityMode,
    CallReach,
    SessionAuthority,
    Trust,
)
from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.context import CredentialKind, Role, TenantContext
from acme.om.steps.types.header import ControlHeader, InputHeader, ModelRequestHeader
from acme.om.steps.types.step import Actor, Origin, Step, StepType

PRINCIPAL_ACTORS = frozenset({Actor.PERSON, Actor.PROGRAM})
"""The actors that speak for a principal: a person, or a program a person or
a service principal runs."""

DATA_TYPES = frozenset({StepType.EVENT, StepType.TOOL_RESPONSE, StepType.SUMMARY})
"""What a model reads as data whoever wrote it: an event from outside, a
tool's output, and a summary of the window."""


def principal_authored(step: Step) -> bool:
    """A message a principal wrote, through a product surface or as an
    automation's trigger. An agent's message, an event, and what the engine
    writes never are, whoever stands behind them."""
    return step.type is StepType.MESSAGE and step.actor in PRINCIPAL_ACTORS


def trust_of(step: Step) -> Trust | None:
    """The tier of what a step tells a model. A principal's message, a
    parent's message to its child, and the engine's notices (a nudge, and
    the notice that the world changed) instruct; every other input, a
    tool's output, and a summary are data. None for a step a model does not
    read as content: its own requests and responses, a control, and the
    other marks of a loop. Only a run writes the engine's message, under its
    epoch: the inbox refuses one (`steps.rules.batch_refusal`)."""
    if step.type is StepType.MESSAGE:
        from_parent = step.actor is Actor.AGENT and step.origin is Origin.PARENT
        notice = step.actor is Actor.ENGINE
        instructs = principal_authored(step) or from_parent or notice
        return Trust.INSTRUCTION if instructs else Trust.DATA
    if step.type is StepType.ENVIRONMENT_CHANGED:
        return Trust.INSTRUCTION
    if step.type in DATA_TYPES:
        return Trust.DATA
    return None


def marks(step: Step) -> bool:
    """Whether a step marks the session it lands in: it is data, or it is an
    input whose header carries the mark: one from an agent whose session
    carried it, or one that carries a file, which is data whoever attached
    it."""
    carried = isinstance(step.header, InputHeader) and step.header.untrusted
    return carried or trust_of(step) is Trust.DATA


def principal_of(ctx: TenantContext) -> Principal:
    """The principal a context speaks for: its user, and the API key it came
    on, which caps the user's role at every call made on what they said. A
    context on any other credential speaks for the person alone."""
    key = ctx.credential_id if ctx.credential_kind is CredentialKind.API_KEY else None
    return Principal(kind=PrincipalKind.PERSON, id=ctx.user_id, key_id=key)


def said_by(step: Step, principal: Principal) -> Step:
    """A principal-authored input as the context that appends it said it:
    its principal is that context's (`principal_of`), key included, whatever
    the caller wrote, so no one speaks, or pays, or asks, in another's name
    or past their key's cap. Any other step is answered as it is."""
    header = step.header
    if not principal_authored(step) or not isinstance(header, InputHeader):
        return step
    if header.principal == principal:
        return step
    return step.model_copy(update={"header": header.model_copy(update={"principal": principal})})


def decided_by(step: Step, user_id: UUID, role: Role) -> Step:
    """A person's decision on a tool call as the context that appends it made
    it: its decider is that context's user, in the role they hold, whatever
    the caller wrote, so no one decides in another's name or above their own
    role. Any other step is answered as it is."""
    header = step.header
    if not isinstance(header, ControlHeader) or header.call is None:
        return step
    if (header.call.decided_by, header.call.role) == (user_id, role):
        return step
    call = header.call.model_copy(update={"decided_by": user_id, "role": role})
    return step.model_copy(update={"header": header.model_copy(update={"call": call})})


def fold(
    speaker: Principal | None, marked: bool, steps: Iterable[Step]
) -> tuple[Principal | None, bool]:
    """The speaker and the mark once `steps` are read, in `seq` order. The
    speaker is the one the latest model request recorded; an input that
    lands after it moves nothing until a request delivers it. The mark,
    once set, stays."""
    for step in steps:
        if isinstance(step.header, ModelRequestHeader):
            speaker = step.header.speaker
        marked = marked or marks(step)
    return speaker, marked


def speaker_after(previous: Principal | None, delivered: Sequence[Step]) -> Principal | None:
    """The speaker a model request records: the principal behind the latest
    principal-authored input among those it delivers, in `seq` order, else
    the one the request before it recorded."""
    for step in delivered:
        if principal_authored(step) and isinstance(step.header, InputHeader):
            previous = step.header.principal
    return previous


def spender_of(passed: Principal | None, speaker: Principal | None) -> Principal | None:
    """Who pays for the next model call: the principal behind the latest
    principal-authored input, else the spender a child's spawn passed it.
    None when neither names one, and then nothing is spent."""
    return speaker if speaker is not None else passed


def call_principal(
    authority: SessionAuthority, speaker: Principal | None, *, child: bool
) -> Principal:
    """Whose authority a tool call runs under: a steady session's fixed
    principal; a delegated session's latest speaker, or its own principal
    before anyone has spoken. A child's calls run under the principal it
    inherited whoever speaks to it, so a message to a child lends it no
    authority its parent's principal lacks; whoever speaks still pays."""
    if authority.mode is AuthorityMode.DELEGATED and speaker is not None and not child:
        return speaker
    return authority.principal


def inherited(
    source: SessionAuthority, speaker: Principal | None, *, from_child: bool, child: bool
) -> tuple[Principal, Principal | None]:
    """The principal and the spender a session takes from the one it came
    from, whose speaker is `speaker` and which is a child itself when
    `from_child`. It runs under the principal that session's calls run
    under, never one its maker names: no child holds more than its parent.
    A child pays as its parent pays; a session handed over pays as the
    principal who confirms its work, so it takes no spender."""
    principal = call_principal(source, speaker, child=from_child)
    return principal, spender_of(source.spender, speaker) if child else None


def needs_person(*, marked: bool, reach: CallReach) -> bool:
    """The rule of two: a session that is marked, holds private data or
    credentials, and asks to act outward needs a person to approve the
    call. Lacking any one of the three, it may run unattended, and class
    policy still applies."""
    return marked and reach.holds_private and reach.outward
