"""Step storage that routes each session by its storage policy, behind the
same interface. Every impl is wired at boot, and a session's policy picks
one: sealed at rest, memory-only with its shape kept, or memory-only with
nothing at rest.

A policy is written once, so the first append of a session with none
writes the default, sealed, and from then on no other can be chosen. A
policy never changes once written, so the routes this process has read
are kept, up to a bound. A revocation does change, so a memory-only
session's is read on every call: its content is under no key, and its
revocation is what this process stops answering."""

from collections import OrderedDict
from collections.abc import Callable, Sequence
from datetime import datetime
from uuid import UUID

from acme.om.base import new_id, utcnow
from acme.om.exceptions import KeyRevoked
from acme.om.privacy.impl.sealed_steps import absent, refuse_sealed, says_something
from acme.om.privacy.storage import PrivacyStorageInterface
from acme.om.privacy.types.session_privacy import SessionPrivacy, StorageMode, StoragePolicy
from acme.om.steps.storage import StepStorageInterface
from acme.om.steps.types.page import StepCursor
from acme.om.steps.types.step import Step

ROUTES_KEPT = 10_000
"""Sessions whose policy this process keeps in hand: each is a few dozen
bytes, and a session past the bound is read again when it next comes."""


class StepStorageRoutedImpl(StepStorageInterface):
    def __init__(
        self,
        sealed: StepStorageInterface,
        shape_only: StepStorageInterface,
        transient: StepStorageInterface,
        policies: PrivacyStorageInterface,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._sealed = sealed
        self._shape_only = shape_only
        self._transient = transient
        self._policies = policies
        self._clock = clock
        self._routes: OrderedDict[tuple[UUID, UUID], StoragePolicy] = OrderedDict()

    async def begin_run(self, org_id: UUID, session_id: UUID) -> int:
        route, _ = await self._route(org_id, session_id, writing=False)
        return await route.begin_run(org_id, session_id)

    async def append_steps(
        self, org_id: UUID, session_id: UUID, epoch: int, steps: Sequence[Step]
    ) -> tuple[Step, ...]:
        refuse_sealed(steps)
        route, in_memory = await self._route(org_id, session_id, writing=True)
        revoked = in_memory and await self._revoked(org_id, session_id, steps)
        stored = await route.append_steps(org_id, session_id, epoch, steps)
        return tuple(_erased(stored) if revoked else stored)

    async def append_inputs(
        self, org_id: UUID, session_id: UUID, steps: Sequence[Step]
    ) -> tuple[Step, ...]:
        refuse_sealed(steps)
        route, in_memory = await self._route(org_id, session_id, writing=True)
        revoked = in_memory and await self._revoked(org_id, session_id, steps)
        stored = await route.append_inputs(org_id, session_id, steps)
        return tuple(_erased(stored) if revoked else stored)

    async def read_steps(
        self, org_id: UUID, session_id: UUID, after_seq: int, limit: int
    ) -> list[Step]:
        route, in_memory = await self._route(org_id, session_id, writing=False)
        stored = await route.read_steps(org_id, session_id, after_seq, limit)
        if in_memory and stored and await self._revoked(org_id, session_id, ()):
            return _erased(stored)
        return stored

    async def read_cursor(self, org_id: UUID, session_id: UUID) -> StepCursor:
        route, _ = await self._route(org_id, session_id, writing=False)
        return await route.read_cursor(org_id, session_id)

    async def purge_history(self, org_id: UUID, session_id: UUID, limit: int) -> int:
        """The session's history at rest, the one its sealed sessions and its
        memory-only sessions that keep their shape share, purged through the
        sealed route. A transient session keeps nothing at rest."""
        return await self._sealed.purge_history(org_id, session_id, limit)

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """What the tenant keeps at rest, purged through the sealed route, as
        `purge_history` is."""
        return await self._sealed.purge_tenant(org_id, limit)

    async def _route(
        self, org_id: UUID, session_id: UUID, *, writing: bool
    ) -> tuple[StepStorageInterface, bool]:
        """The impl the session's policy names, and whether its content lives
        in this process. A write fixes the default policy on a session with
        none; a read of one with none reads it as sealed and keeps nothing,
        since its policy may still be chosen."""
        policy = self._routes.get((org_id, session_id))
        if policy is None:
            if writing:
                record = await self._policies.create_privacy(
                    org_id,
                    SessionPrivacy(id=new_id(), session_id=session_id, created_at=self._clock()),
                )
            else:
                record = await self._policies.read_privacy(org_id, session_id)
            if record is None:
                return self._sealed, False
            policy = record.policy
            self._routes[(org_id, session_id)] = policy
            if len(self._routes) > ROUTES_KEPT:
                self._routes.popitem(last=False)
        else:
            self._routes.move_to_end((org_id, session_id))
        if policy.mode is StorageMode.SEALED:
            return self._sealed, False
        return (self._shape_only if policy.keep_shape else self._transient), True

    async def _revoked(self, org_id: UUID, session_id: UUID, steps: Sequence[Step]) -> bool:
        """Whether a memory-only session's key is revoked, read afresh, since a
        revocation comes after the policy. Such a session takes nothing that
        says something, as a sealed one does not (`KeyRevoked`), and what this
        process still holds of it reads as absent."""
        record = await self._policies.read_privacy(org_id, session_id)
        if record is None or record.revoked_at is None:
            return False
        if any(says_something(step) for step in steps):
            raise KeyRevoked(f"the key of session {session_id} is revoked")
        return True


def _erased(steps: Sequence[Step]) -> list[Step]:
    return [absent(step) if says_something(step) else step for step in steps]
