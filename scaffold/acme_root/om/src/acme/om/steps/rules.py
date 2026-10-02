"""Pure rules of the history: what one append may hold, and where a person's
step came in. Values in, values out; both storage impls ask them before
they write."""

from collections.abc import Sequence
from uuid import UUID

from acme.om.context import AppType
from acme.om.steps.types.step import Actor, Origin, Step


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
