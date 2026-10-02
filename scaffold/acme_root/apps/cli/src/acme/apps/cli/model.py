"""Pure: how a change is told, how a size reads, and which org a slug names.
Values in, values out; no client, no clock, no terminal, so every rule is
unit tested without either."""

from collections.abc import Sequence
from uuid import UUID

from acme.client.types import (
    AgentSessionView,
    LoopOutcome,
    MembershipChoiceView,
    OrgKind,
    SessionStatus,
    StepType,
    StepView,
)


def describe(kind: str, target_id: UUID, actor: str) -> str:
    """One line that says who did what to which record. The kind is
    "<namespace>.<entity>.<action>", and the action is already in the past
    tense, so the line reads "Ann created tenancy.invitation <id>" for any
    namespace. A kind of another shape is told as it is."""
    namespace, _, rest = kind.partition(".")
    entity, _, action = rest.rpartition(".")
    if not (namespace and entity and action):
        return f"{actor}: {kind} {target_id}"
    return f"{actor} {action} {namespace}.{entity} {target_id}"


def human_size(size_bytes: int) -> str:
    """Bytes as a person reads them: B under a kilobyte, then KB and MB with
    one decimal, powers of 1024."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def choose_org(
    memberships: Sequence[MembershipChoiceView], slug: str | None
) -> MembershipChoiceView:
    """The org a sign-in or a switch enters: the one `slug` names, or, when no
    slug is given, the only one, else the person's personal org, the place
    every person has. Anything else is a LookupError whose message is the
    slugs to choose from."""
    choices = [m for m in memberships if slug is None or m.org.slug == slug]
    if len(choices) == 1:
        return choices[0]
    personal = [m for m in choices if slug is None and m.org.kind is OrgKind.personal]
    if len(personal) == 1:
        return personal[0]
    raise LookupError(", ".join(sorted(m.org.slug for m in memberships)) or "none")


def org_lines(memberships: Sequence[MembershipChoiceView], current: str | None) -> str:
    """One line per org, by name, the current one marked with `*` and the
    person's personal org said as such."""
    ordered = sorted(memberships, key=lambda m: (m.org.name.lower(), m.org.slug))
    width = max((len(m.org.slug) for m in ordered), default=0)
    return "\n".join(
        f"{'*' if m.org.slug == current else ' '} {m.org.slug:<{width}}"
        f"  {m.org.name} ({m.role.value}{', personal' if m.org.kind is OrgKind.personal else ''})"
        for m in ordered
    )


LINE_TEXT = 120
"""The most characters of a step's text one line shows."""


def step_line(step: StepView) -> str:
    """One line for a step of a session's history: its seq, its type, and
    what it says or did, the text cut to one line."""
    said = " ".join(step.text.split())
    if len(said) > LINE_TEXT:
        said = said[: LINE_TEXT - 1] + "…"
    detail = said
    if step.type is StepType.model_response and step.tools:
        detail = f"{said} [calls {', '.join(step.tools)}]".strip()
    elif step.type is StepType.tool_request:
        detail = step.tool or ""
    elif step.type is StepType.tool_response and step.failure is not None:
        detail = f"{step.failure.value}: {said}"
    elif step.command is not None:
        detail = step.command.value
    elif step.park is not None:
        detail = f"{step.park.reason.value}, until {step.park.unlock}"
    elif step.outcome is not None:
        detail = step.outcome.value
    return f"{step.seq:>5}  {step.type.value:<15} {detail}".rstrip()


def settled(session: AgentSessionView) -> bool:
    """Whether a session's loop has stopped moving: it ended, or it waits on
    an unlock."""
    return session.status in (SessionStatus.idle, SessionStatus.parked)


def follow_succeeded(session: AgentSessionView, steps: Sequence[StepView]) -> bool:
    """Whether a followed loop came to what was asked: the session is idle
    and the last loop it ended succeeded. A park, or any other outcome, is
    not."""
    if session.status is not SessionStatus.idle:
        return False
    ended = [step for step in steps if step.type is StepType.loop_ended]
    return bool(ended) and ended[-1].outcome is LoopOutcome.succeeded
