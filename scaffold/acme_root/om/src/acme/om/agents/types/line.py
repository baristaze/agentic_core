"""What a tool that asks for a leased resource answers: the request it
joined the line with, and its lease when the ask was granted at once, or
its place and the estimate of its wait. A tool whose output is this shape,
or one built on it, is a tool that asks in line, and its loop parks on
`resource` when its turn ends while the request still waits."""

from typing import Self
from uuid import UUID

from pydantic import Field

from acme.om.base import Platform
from acme.om.leases.types.request import Standing


class InLine(Platform):
    request_id: UUID
    lease_id: UUID | None = None
    token: int | None = None  # the fencing token every act on the resource presents
    place: int | None = Field(default=None, ge=1)  # 1 is next in some line it stands in
    estimate_seconds: float | None = Field(default=None, ge=0)

    @classmethod
    def of(cls, standing: Standing) -> Self:
        """The answer an ask's standing gives the model."""
        lease = standing.lease
        return cls(
            request_id=standing.request.id,
            lease_id=None if lease is None else lease.id,
            token=None if lease is None else lease.token,
            place=standing.place,
            estimate_seconds=standing.estimate_seconds,
        )
