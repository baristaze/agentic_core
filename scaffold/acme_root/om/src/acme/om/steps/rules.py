"""Pure rules of the history: what one append may hold. Values in, values
out; both storage impls ask them before they write."""

from collections.abc import Sequence
from uuid import UUID

from acme.om.steps.types.step import Step


def batch_refusal(session_id: UUID, steps: Sequence[Step], *, inputs_only: bool) -> str | None:
    """Why a batch may not be appended to `session_id`, or None when it may.
    Every step names the session it is appended to, no id comes twice, and
    the inbox's append (`inputs_only`) holds inputs and controls alone: only
    a run, under its writer epoch, appends what the engine writes."""
    if len({step.id for step in steps}) != len(steps):
        return "an append names one step id twice"
    if any(step.session_id != session_id for step in steps):
        return f"a step names another session than {session_id}"
    if inputs_only and any(not (s.type.is_input() or s.type.is_control()) for s in steps):
        return "the inbox appends inputs and controls alone; a run appends the rest"
    return None
