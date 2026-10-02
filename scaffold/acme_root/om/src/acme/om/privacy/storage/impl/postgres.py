from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, Result, Select, Table, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from acme.om.exceptions import KeyRevoked, NotFound, TenantMismatch, UniqueKeyTaken
from acme.om.outbox.storage.tables.outbox_rows import OutboxRows
from acme.om.outbox.types.row import OutboxRow
from acme.om.privacy.storage import PrivacyStorageInterface
from acme.om.privacy.storage.tables.session_keys import SessionKeys
from acme.om.privacy.storage.tables.session_privacy import SessionPrivacyRows
from acme.om.privacy.types.session_privacy import KeyRing, SessionKey, SessionPrivacy
from acme.om.storage.impl.pg_base import PgStorageBase, delete_batch, deleted, violated_constraint
from acme.om.storage.utils.translation import to_model, to_row, to_values


def rowcount(result: Result[Any]) -> int:
    """The rows an UPDATE changed, as the driver reports them."""
    return cast(CursorResult[Any], result).rowcount


class PrivacyStoragePostgresImpl(PgStorageBase, PrivacyStorageInterface):
    async def create_privacy(
        self, org_id: UUID, record: SessionPrivacy, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> SessionPrivacy:
        """The insert meets the session's unique key when the tenant holds a
        record of it, and lands nothing; another tenant's id is the primary
        key's, and refused."""
        insert = (
            pg_insert(SessionPrivacyRows)
            .values(org_id=org_id, **to_values(record, SessionPrivacyRows))
            .on_conflict_do_nothing(
                index_elements=[SessionPrivacyRows.org_id, SessionPrivacyRows.session_id]
            )
            .returning(SessionPrivacyRows.id)
        )
        async with self._session_for(SessionPrivacyRows, org_id=org_id) as session:
            try:
                landed = (await session.execute(insert)).scalar_one_or_none() is not None
                if landed:
                    for outbox_row in outbox_rows:
                        session.add(to_row(outbox_row, OutboxRows, org_id=org_id))
                    await session.commit()
                    return record
                stored = (
                    await session.execute(self._record(org_id, record.session_id))
                ).scalar_one()
                await session.commit()
                return to_model(stored, SessionPrivacy)
            except IntegrityError as error:
                await session.rollback()
                raise TenantMismatch(f"session privacy {record.id} is not in {org_id}") from error

    @staticmethod
    def _record(org_id: UUID, session_id: UUID) -> Select[tuple[SessionPrivacyRows]]:
        return select(SessionPrivacyRows).where(
            SessionPrivacyRows.org_id == org_id, SessionPrivacyRows.session_id == session_id
        )

    async def read_privacy(self, org_id: UUID, session_id: UUID) -> SessionPrivacy | None:
        stmt = self._record(org_id, session_id)
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, SessionPrivacy)

    async def add_key(self, org_id: UUID, key: SessionKey) -> SessionKey:
        """The record's row lock first, as the revocation takes it, so a
        version and a revocation of one session queue on it, and a version
        never lands after the key is revoked."""
        lock = (
            select(SessionPrivacyRows.revoked_at)
            .where(
                SessionPrivacyRows.org_id == org_id,
                SessionPrivacyRows.session_id == key.session_id,
            )
            .with_for_update()
        )
        insert = (
            pg_insert(SessionKeys)
            .values(org_id=org_id, **to_values(key, SessionKeys))
            .on_conflict_do_nothing(
                index_elements=[SessionKeys.org_id, SessionKeys.session_id, SessionKeys.version]
            )
            .returning(SessionKeys)
        )
        async with self._session_for(SessionKeys, org_id=org_id) as session:
            found = (await session.execute(lock)).one_or_none()
            if found is None:
                raise NotFound(f"no privacy record for session {key.session_id}")
            if found.revoked_at is not None:
                raise KeyRevoked(f"the key of session {key.session_id} is revoked")
            try:
                row = (await session.execute(insert)).scalar_one_or_none()
                if row is None:
                    row = (
                        await session.execute(
                            select(SessionKeys).where(
                                SessionKeys.org_id == org_id,
                                SessionKeys.session_id == key.session_id,
                                SessionKeys.version == key.version,
                            )
                        )
                    ).scalar_one()
                stored = to_model(row, SessionKey)
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                if (
                    violated_constraint(error)
                    == cast(Table, SessionKeys.__table__).primary_key.name
                ):
                    raise TenantMismatch(f"session key {key.id} is not in {org_id}") from error
                raise UniqueKeyTaken(f"session key {key.id} is taken") from error
            return stored

    async def read_keys(self, org_id: UUID, session_id: UUID) -> KeyRing:
        revoked = select(SessionPrivacyRows.revoked_at).where(
            SessionPrivacyRows.org_id == org_id, SessionPrivacyRows.session_id == session_id
        )
        keys = (
            select(SessionKeys)
            .where(SessionKeys.org_id == org_id, SessionKeys.session_id == session_id)
            .order_by(SessionKeys.version)
        )
        async with self._session_for(SessionKeys, org_id=org_id) as session:
            revoked_at = (await session.execute(revoked)).scalar_one_or_none()
            rows = (await session.execute(keys)).scalars().all()
            return KeyRing(
                revoked=revoked_at is not None,
                keys=tuple(to_model(row, SessionKey) for row in rows),
            )

    async def revoke(
        self, org_id: UUID, record: SessionPrivacy, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> SessionPrivacy:
        at = record.revoked_at
        if at is None:
            raise ValueError("a revocation names when it happened")
        written = (
            pg_insert(SessionPrivacyRows)
            .values(org_id=org_id, **to_values(record, SessionPrivacyRows))
            .on_conflict_do_nothing(
                index_elements=[SessionPrivacyRows.org_id, SessionPrivacyRows.session_id]
            )
            .returning(SessionPrivacyRows.id)
        )
        mark = (
            update(SessionPrivacyRows)
            .where(
                SessionPrivacyRows.org_id == org_id,
                SessionPrivacyRows.session_id == record.session_id,
                SessionPrivacyRows.revoked_at.is_(None),
            )
            .values(revoked_at=at, revoked_by=record.revoked_by)
        )
        destroy = (
            update(SessionKeys)
            .where(
                SessionKeys.org_id == org_id,
                SessionKeys.session_id == record.session_id,
                SessionKeys.destroyed_at.is_(None),
            )
            .values(wrapped=None, wrapping=None, wrapped_at=None, destroyed_at=at)
        )
        async with self._session_for(SessionPrivacyRows, org_id=org_id) as session:
            try:
                landed = (await session.execute(written)).scalar_one_or_none() is not None
            except IntegrityError as error:
                await session.rollback()
                raise TenantMismatch(f"session privacy {record.id} is not in {org_id}") from error
            marked = rowcount(await session.execute(mark)) == 1
            row = (await session.execute(self._record(org_id, record.session_id))).scalar_one()
            stored = to_model(row, SessionPrivacy)
            await session.execute(destroy)
            if landed or marked:
                for outbox_row in outbox_rows:
                    session.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            await session.commit()
            return stored

    async def rewrap_key(self, org_id: UUID, key: SessionKey, expected: bytes) -> bool:
        stmt = (
            update(SessionKeys)
            .where(
                SessionKeys.org_id == org_id,
                SessionKeys.id == key.id,
                SessionKeys.wrapped == expected,
                SessionKeys.destroyed_at.is_(None),
            )
            .values(wrapped=key.wrapped, wrapping=key.wrapping, wrapped_at=key.wrapped_at)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            changed = rowcount(await session.execute(stmt))
            await session.commit()
            return changed == 1

    async def read_keys_wrapped_before(
        self, org_id: UUID, before: datetime, limit: int
    ) -> list[SessionKey]:
        stmt = (
            select(SessionKeys)
            .where(
                SessionKeys.org_id == org_id,
                SessionKeys.destroyed_at.is_(None),
                SessionKeys.wrapped_at < before,
            )
            .order_by(SessionKeys.wrapped_at, SessionKeys.id)
            .limit(limit)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            return [to_model(row, SessionKey) for row in (await session.execute(stmt)).scalars()]

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """The versions first, so a record never goes while a version of its
        key is left."""
        gone = 0
        for table in (SessionKeys, SessionPrivacyRows):
            if gone >= limit:
                break
            stmt = delete_batch(table, table.org_id == org_id, limit=limit - gone)
            async with self._session_for(stmt, org_id=org_id) as session:
                gone += deleted(await session.execute(stmt))
                await session.commit()
        return gone
