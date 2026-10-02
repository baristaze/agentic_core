"""Pure rules of fill sets: the version a switch makes, and the fallback a
failed fill falls to. Values in, values out."""

from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from acme.om.base import derived_id
from acme.om.models.types.fill import Fill, FillSet, FillSwitch, ModelRole, SwitchReason


def switch_refusal(head: FillSet, role: ModelRole, to: Fill) -> str | None:
    """Why `role` may not switch to `to` from `head`, or None when it may."""
    current = head.fill_for(role)
    if current is None:
        return f"the fill set holds no model role {role}"
    if current == to:
        return f"{to.name} already serves {role}"
    if not head.eligibility.admits(to.eligibility):
        return f"{to.name} does not meet the session's eligibility"
    return None


def switched(head: FillSet, fills: FillSwitch, step_id: UUID, at: datetime) -> FillSet:
    """The version after `head` that the `switched` step `step_id` announces:
    the role's fill replaced, and the new fill no longer among its fallbacks.
    Its id is derived from the step, so writing it twice writes it once."""
    if fills.fill_set_version != head.version + 1:
        raise ValueError(
            f"the switch makes version {fills.fill_set_version}, and the head is {head.version}"
        )
    if head.fill_for(fills.role) != fills.from_fill:
        raise ValueError(f"the switch starts from a fill {fills.role} no longer holds")
    roles = tuple(
        r.model_copy(
            update={
                "fill": fills.to_fill,
                "fallbacks": tuple(f for f in r.fallbacks if f != fills.to_fill),
            }
        )
        if r.role == fills.role
        else r
        for r in head.roles
    )
    return FillSet(
        id=derived_id(step_id, at, "fill_set"),
        created_at=at,
        session_id=head.session_id,
        version=fills.fill_set_version,
        roles=roles,
        eligibility=head.eligibility,
        reason=fills.reason,
        switched_by=step_id,
    )


def fill_switch(head: FillSet, role: ModelRole, to: Fill, reason: SwitchReason) -> FillSwitch:
    """What the `switched` step of a switch from `head` says."""
    current = head.fill_for(role)
    if current is None:
        raise ValueError(f"the fill set holds no model role {role}")
    return FillSwitch(
        role=role,
        from_fill=current,
        to_fill=to,
        fill_set_version=head.version + 1,
        reason=reason,
    )


def next_fallback(head: FillSet, role: ModelRole, tried: Collection[Fill] = ()) -> Fill | None:
    """The first of the role's declared fallbacks the session's eligibility
    admits and no call has tried, or None when none is left."""
    found = head.role_fill(role)
    if found is None:
        return None
    return next(
        (
            f
            for f in found.fallbacks
            if f not in tried and f != found.fill and head.eligibility.admits(f.eligibility)
        ),
        None,
    )
