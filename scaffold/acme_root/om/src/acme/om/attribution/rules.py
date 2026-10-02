"""Pure rules of attribution: which input a principal wrote, which content
instructs and which is data, who pays for the next model call, whose
authority a tool call runs under, and when a convinced model needs a
person. Values in, values out; no clock, no storage.

The session caches two answers its steps give, folded in `seq` order with
its status (`agent_sessions.rules.after_step`): the speaker, the
principal behind the latest principal-authored input, and the untrusted
mark. A reader that needs them now folds the steps after the cache's last
`seq` with `fold`.

The mark is set when the first data lands, no later than the model reads
it: the next model request delivers every input that waits, and a tool's
response is read by the request after it. So no tool call is ever decided
on a mark the model's reading has outrun."""

from collections.abc import Iterable

from acme.om.attribution.types.authority import (
    AuthorityMode,
    CallReach,
    SessionAuthority,
    Trust,
)
from acme.om.attribution.types.principal import Principal
from acme.om.steps.types.header import InputHeader
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
    parent's message to its child, and the engine's notice that the world
    changed instruct; every other input, a tool's output, and a summary are
    data. None for a step a model does not read as content: its own
    requests and responses, a control, and the other marks of a loop."""
    if step.type is StepType.MESSAGE:
        from_parent = step.actor is Actor.AGENT and step.origin is Origin.PARENT
        return Trust.INSTRUCTION if principal_authored(step) or from_parent else Trust.DATA
    if step.type is StepType.ENVIRONMENT_CHANGED:
        return Trust.INSTRUCTION
    if step.type in DATA_TYPES:
        return Trust.DATA
    return None


def marks(step: Step) -> bool:
    """Whether a step marks the session it lands in: it is data, or it is an
    input from an agent whose session carried the mark."""
    carried = isinstance(step.header, InputHeader) and step.header.untrusted
    return carried or trust_of(step) is Trust.DATA


def fold(
    speaker: Principal | None, marked: bool, steps: Iterable[Step]
) -> tuple[Principal | None, bool]:
    """The speaker and the mark once `steps` are read, in `seq` order. A
    principal's message makes its principal the speaker; nothing else moves
    it. The mark, once set, stays."""
    for step in steps:
        if principal_authored(step) and isinstance(step.header, InputHeader):
            speaker = step.header.principal
        marked = marked or marks(step)
    return speaker, marked


def spender_of(passed: Principal | None, speaker: Principal | None) -> Principal | None:
    """Who pays for the next model call: the principal behind the latest
    principal-authored input, else the spender a child's spawn passed it.
    None when neither names one, and then nothing is spent."""
    return speaker if speaker is not None else passed


def call_principal(authority: SessionAuthority, speaker: Principal | None) -> Principal:
    """Whose authority a tool call runs under: a steady session's fixed
    principal; a delegated session's latest speaker, or its own principal
    before anyone has spoken."""
    if authority.mode is AuthorityMode.DELEGATED and speaker is not None:
        return speaker
    return authority.principal


def inherited(
    source: SessionAuthority, speaker: Principal | None, *, child: bool
) -> tuple[Principal, Principal | None]:
    """The principal and the spender a session takes from the one it came
    from, whose speaker is `speaker`. It runs under the principal that
    session's calls run under, never one its maker names: no child holds
    more than its parent. A child pays as its parent pays; a session handed
    over pays as the principal who confirms its work, so it takes no
    spender."""
    principal = call_principal(source, speaker)
    return principal, spender_of(source.spender, speaker) if child else None


def needs_person(*, marked: bool, reach: CallReach) -> bool:
    """The rule of two: a session that is marked, holds private data or
    credentials, and asks to act outward needs a person to approve the
    call. Lacking any one of the three, it may run unattended, and class
    policy still applies."""
    return marked and reach.holds_private and reach.outward
