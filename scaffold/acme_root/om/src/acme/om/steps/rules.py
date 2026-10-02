"""Pure rules of the history: what one append may hold, where a person's
step came in, and the steps a person writes through a product surface.
Values in, values out; both storage impls ask them before they write."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.context import AppType, CredentialKind, TenantContext
from acme.om.steps.types.content import Content, TextBlock
from acme.om.steps.types.header import ControlCommand, ControlHeader, InputHeader
from acme.om.steps.types.step import Actor, Origin, Step, StepType


def batch_refusal(session_id: UUID, steps: Sequence[Step], *, inputs_only: bool) -> str | None:
    """Why a batch may not be appended to `session_id`, or None when it may.
    Every step names the session it is appended to, no id comes twice, and
    the inbox's append (`inputs_only`) holds inputs and controls alone, and
    no input the engine wrote: only a run, under its writer epoch, appends
    what the engine writes, its notices included."""
    if len({step.id for step in steps}) != len(steps):
        return "an append names one step id twice"
    if any(step.session_id != session_id for step in steps):
        return f"a step names another session than {session_id}"
    if inputs_only and any(not (s.type.is_input() or s.type.is_control()) for s in steps):
        return "the inbox appends inputs and controls alone; a run appends the rest"
    if inputs_only and any(s.type.is_input() and s.actor is Actor.ENGINE for s in steps):
        return "the engine's notice is a run's to write, under its epoch"
    return None


ORIGINS: dict[AppType, Origin] = {
    AppType.PORTAL: Origin.PORTAL,
    AppType.ADMIN: Origin.API,
    AppType.CLI: Origin.CLI,
    AppType.API: Origin.API,
    AppType.WORKER: Origin.AUTOMATION,
}
"""Where a person's step came in, by the app that carried it."""


def origin_of(app: AppType) -> Origin:
    """The origin of a step a person wrote through `app`."""
    return ORIGINS[app]


DECIDED = frozenset({ControlCommand.APPROVE, ControlCommand.DENY})
"""The controls that decide one tool call, which only the tools manager
writes, bound to the call they decide (`ToolsManagerInterface.decide_call`)."""


def actor_of(credential: CredentialKind) -> Actor:
    """Who wrote a principal's step: a program on the principal's API key,
    or the person themselves on any other credential."""
    return Actor.PROGRAM if credential is CredentialKind.API_KEY else Actor.PERSON


def message_step(
    step_id: UUID, now: datetime, session_id: UUID, ctx: TenantContext, text: str
) -> Step:
    """A principal's message, said through the surface `ctx` arrived on and
    in its user's name; it wakes as a message does by default. It arrives
    outside any run, so its loop id is its own."""
    return Step(
        id=step_id,
        created_at=now,
        session_id=session_id,
        loop_id=step_id,
        type=StepType.MESSAGE,
        actor=actor_of(ctx.credential_kind),
        origin=origin_of(ctx.app.type),
        header=InputHeader(principal=Principal(kind=PrincipalKind.PERSON, id=ctx.user_id)),
        content=Content(blocks=(TextBlock(text=text),)),
    )


def control_step(
    step_id: UUID, now: datetime, session_id: UUID, ctx: TenantContext, command: ControlCommand
) -> Step:
    """A principal's control, out of band, through the surface `ctx` arrived
    on. A decision on a tool call is the tools manager's to write, bound to
    its call, and is refused here (`ValueError`)."""
    if command in DECIDED:
        raise ValueError(f"a {command.value} decides one tool call; the tools manager writes it")
    return Step(
        id=step_id,
        created_at=now,
        session_id=session_id,
        loop_id=step_id,
        type=StepType.CONTROL,
        actor=actor_of(ctx.credential_kind),
        origin=origin_of(ctx.app.type),
        header=ControlHeader(command=command),
    )
