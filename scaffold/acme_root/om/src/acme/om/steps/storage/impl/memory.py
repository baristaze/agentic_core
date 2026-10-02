from collections.abc import Sequence
from uuid import UUID

from acme.om.exceptions import StaleWriter, UniqueKeyTaken, ValidationFailed
from acme.om.steps.rules import batch_refusal
from acme.om.steps.storage import StepStorageInterface
from acme.om.steps.types.page import StepCursor
from acme.om.steps.types.step import Step
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable


class StepStorageMemoryImpl(MemoryStorageBase, StepStorageInterface):
    def __init__(self) -> None:
        super().__init__()
        self._steps: MemoryTable[Step] = {}
        # The twin of the cursor rows: per tenant and session, the last seq
        # assigned and the epoch of the run that holds the session.
        self._cursors: dict[tuple[UUID, UUID], StepCursor] = {}

    async def begin_run(self, org_id: UUID, session_id: UUID) -> int:
        async with self._lock:
            cursor = self._cursors.get((org_id, session_id), StepCursor())
            moved = cursor.model_copy(update={"epoch": cursor.epoch + 1})
            self._cursors[(org_id, session_id)] = moved
            return moved.epoch

    async def append_steps(
        self, org_id: UUID, session_id: UUID, epoch: int, steps: Sequence[Step]
    ) -> tuple[Step, ...]:
        return await self._append(org_id, session_id, steps, epoch)

    async def append_inputs(
        self, org_id: UUID, session_id: UUID, steps: Sequence[Step]
    ) -> tuple[Step, ...]:
        return await self._append(org_id, session_id, steps, None)

    async def _append(
        self, org_id: UUID, session_id: UUID, steps: Sequence[Step], epoch: int | None
    ) -> tuple[Step, ...]:
        batch = list(steps)
        refusal = batch_refusal(session_id, batch, inputs_only=epoch is None)
        if refusal is not None:
            raise ValidationFailed(refusal)
        async with self._lock:
            stored: dict[UUID, Step] = {}
            for step in batch:
                found = self._get(self._steps, org_id, step.id)
                if found is None:
                    continue
                if found.session_id != session_id:
                    raise UniqueKeyTaken(f"step {step.id} is another session's")
                stored[step.id] = found
            fresh = [step for step in batch if step.id not in stored]
            if not fresh:
                return tuple(stored[step.id] for step in batch)
            # Every refusal comes before a number is spent, as Postgres hands
            # out none when it refuses: first the epoch, which the cursor row
            # checks before the insert runs, then another tenant's id, which
            # the insert meets.
            key = (org_id, session_id)
            cursor = self._cursors.get(key)
            if epoch is not None and (cursor is None or cursor.epoch != epoch):
                raise StaleWriter(f"session {session_id} is not held at epoch {epoch}")
            for step in fresh:
                self._fence(self._steps, org_id, step)
            head = cursor.head if cursor is not None else 0
            for seq, step in enumerate(fresh, start=head + 1):
                appended = step.model_copy(update={"seq": seq})
                self._put(self._steps, org_id, appended)
                stored[step.id] = appended
            self._cursors[key] = StepCursor(
                head=head + len(fresh), epoch=cursor.epoch if cursor is not None else 0
            )
            return tuple(stored[step.id] for step in batch)

    async def read_steps(
        self, org_id: UUID, session_id: UUID, after_seq: int, limit: int
    ) -> list[Step]:
        newer = [
            step
            for step in self._rows(self._steps, org_id)
            if step.session_id == session_id and step.seq > after_seq
        ]
        return sorted(newer, key=lambda step: step.seq)[:limit]

    async def purge_history(self, org_id: UUID, session_id: UUID, limit: int) -> int:
        async with self._lock:
            gone = [s.id for s in self._rows(self._steps, org_id) if s.session_id == session_id]
            for step_id in gone[:limit]:
                del self._steps[step_id]
            if len(gone) >= limit:
                return limit
            return len(gone) + (self._cursors.pop((org_id, session_id), None) is not None)

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        async with self._lock:
            gone = [s.id for s in self._rows(self._steps, org_id)][:limit]
            for step_id in gone:
                del self._steps[step_id]
            cursors = [key for key in self._cursors if key[0] == org_id][: limit - len(gone)]
            for key in cursors:
                del self._cursors[key]
            return len(gone) + len(cursors)

    async def read_cursor(self, org_id: UUID, session_id: UUID) -> StepCursor:
        return self._cursors.get((org_id, session_id), StepCursor())
