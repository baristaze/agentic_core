from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, Result, Select, Table, and_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from acme.om.exceptions import KeyRevoked, NotFound, TenantMismatch, UniqueKeyTaken
from acme.om.outbox.storage.tables.outbox_rows import OutboxRows
from acme.om.outbox.types.row import OutboxRow
from acme.om.privacy.storage import PrivacyStorageInterface
from acme.om.privacy.storage.tables.agent_session_keys import AgentSessionKeys
from acme.om.privacy.storage.tables.agent_session_privacy import AgentSessionPrivacyRows
from acme.om.privacy.types.agent_session_privacy import (
    AgentSessionKey,
    AgentSessionPrivacy,
    KeyRing,
)
from acme.om.storage.impl.pg_base import PgStorageBase, delete_batch, deleted, violated_constraint
from acme.om.storage.utils.translation import to_model, to_row, to_values


def rowcount(result: Result[Any]) -> int:
    """The rows an UPDATE changed, as the driver reports them."""
    return cast(CursorResult[Any], result).rowcount


class PrivacyStoragePostgresImpl(PgStorageBase, PrivacyStorageInterface):
    async def create_privacy(
        self, org_id: UUID, record: AgentSessionPrivacy, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> AgentSessionPrivacy:
        """The insert meets the session's unique key when the tenant holds a
        record of it, and lands nothing; another tenant's id is the primary
        key's, and refused."""
        insert = (
            pg_insert(AgentSessionPrivacyRows)
            .values(org_id=org_id, **to_values(record, AgentSessionPrivacyRows))
            .on_conflict_do_nothing(
                index_elements=[
                    AgentSessionPrivacyRows.org_id,
                    AgentSessionPrivacyRows.agent_session_id,
                ]
            )
            .returning(AgentSessionPrivacyRows.id)
        )
        async with self._session_for(AgentSessionPrivacyRows, org_id=org_id) as session:
            try:
                landed = (await session.execute(insert)).scalar_one_or_none() is not None
                if landed:
                    for outbox_row in outbox_rows:
                        session.add(to_row(outbox_row, OutboxRows, org_id=org_id))
                    await session.commit()
                    return record
                stored = (
                    await session.execute(self._record(org_id, record.agent_session_id))
                ).scalar_one()
                await session.commit()
                return to_model(stored, AgentSessionPrivacy)
            except IntegrityError as error:
                await session.rollback()
                raise TenantMismatch(f"session privacy {record.id} is not in {org_id}") from error

    @staticmethod
    def _record(org_id: UUID, agent_session_id: UUID) -> Select[tuple[AgentSessionPrivacyRows]]:
        return select(AgentSessionPrivacyRows).where(
            AgentSessionPrivacyRows.org_id == org_id,
            AgentSessionPrivacyRows.agent_session_id == agent_session_id,
        )

    async def read_privacy(
        self, org_id: UUID, agent_session_id: UUID
    ) -> AgentSessionPrivacy | None:
        stmt = self._record(org_id, agent_session_id)
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, AgentSessionPrivacy)

    async def add_key(self, org_id: UUID, key: AgentSessionKey) -> AgentSessionKey:
        """The record's row lock first, as the revocation takes it, so a
        version and a revocation of one session queue on it, and a version
        never lands after the key is revoked."""
        lock = (
            select(AgentSessionPrivacyRows.revoked_at)
            .where(
                AgentSessionPrivacyRows.org_id == org_id,
                AgentSessionPrivacyRows.agent_session_id == key.agent_session_id,
            )
            .with_for_update()
        )
        insert = (
            pg_insert(AgentSessionKeys)
            .values(org_id=org_id, **to_values(key, AgentSessionKeys))
            .on_conflict_do_nothing(
                index_elements=[
                    AgentSessionKeys.org_id,
                    AgentSessionKeys.agent_session_id,
                    AgentSessionKeys.version,
                ]
            )
            .returning(AgentSessionKeys)
        )
        async with self._session_for(AgentSessionKeys, org_id=org_id) as session:
            found = (await session.execute(lock)).one_or_none()
            if found is None:
                raise NotFound(f"no privacy record for session {key.agent_session_id}")
            if found.revoked_at is not None:
                raise KeyRevoked(f"the key of session {key.agent_session_id} is revoked")
            try:
                row = (await session.execute(insert)).scalar_one_or_none()
                if row is None:
                    row = (
                        await session.execute(
                            select(AgentSessionKeys).where(
                                AgentSessionKeys.org_id == org_id,
                                AgentSessionKeys.agent_session_id == key.agent_session_id,
                                AgentSessionKeys.version == key.version,
                            )
                        )
                    ).scalar_one()
                stored = to_model(row, AgentSessionKey)
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                if (
                    violated_constraint(error)
                    == cast(Table, AgentSessionKeys.__table__).primary_key.name
                ):
                    raise TenantMismatch(f"session key {key.id} is not in {org_id}") from error
                raise UniqueKeyTaken(f"session key {key.id} is taken") from error
            return stored

    async def read_keys(self, org_id: UUID, agent_session_id: UUID) -> KeyRing:
        """One statement: the record, and each version beside it. A version
        lands only under its session's record (`add_key`), so a session
        with no record holds no version."""
        ring = (
            select(AgentSessionPrivacyRows.revoked_at, AgentSessionKeys)
            .select_from(AgentSessionPrivacyRows)
            .outerjoin(
                AgentSessionKeys,
                and_(
                    AgentSessionKeys.org_id == AgentSessionPrivacyRows.org_id,
                    AgentSessionKeys.agent_session_id == AgentSessionPrivacyRows.agent_session_id,
                ),
            )
            .where(
                AgentSessionPrivacyRows.org_id == org_id,
                AgentSessionPrivacyRows.agent_session_id == agent_session_id,
            )
            .order_by(AgentSessionKeys.version)
        )
        async with self._session_for(AgentSessionKeys, org_id=org_id) as session:
            rows = (await session.execute(ring)).all()
        if not rows:
            return KeyRing()
        return KeyRing(
            revoked=rows[0].revoked_at is not None,
            keys=tuple(
                to_model(row.AgentSessionKeys, AgentSessionKey)
                for row in rows
                if row.AgentSessionKeys is not None
            ),
        )

    async def revoke(
        self, org_id: UUID, record: AgentSessionPrivacy, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> AgentSessionPrivacy:
        at = record.revoked_at
        if at is None:
            raise ValueError("a revocation names when it happened")
        written = (
            pg_insert(AgentSessionPrivacyRows)
            .values(org_id=org_id, **to_values(record, AgentSessionPrivacyRows))
            .on_conflict_do_nothing(
                index_elements=[
                    AgentSessionPrivacyRows.org_id,
                    AgentSessionPrivacyRows.agent_session_id,
                ]
            )
            .returning(AgentSessionPrivacyRows.id)
        )
        mark = (
            update(AgentSessionPrivacyRows)
            .where(
                AgentSessionPrivacyRows.org_id == org_id,
                AgentSessionPrivacyRows.agent_session_id == record.agent_session_id,
                AgentSessionPrivacyRows.revoked_at.is_(None),
            )
            .values(revoked_at=at, revoked_by=record.revoked_by)
        )
        destroy = (
            update(AgentSessionKeys)
            .where(
                AgentSessionKeys.org_id == org_id,
                AgentSessionKeys.agent_session_id == record.agent_session_id,
                AgentSessionKeys.destroyed_at.is_(None),
            )
            .values(wrapped=None, wrapping=None, wrapped_at=None, destroyed_at=at)
        )
        async with self._session_for(AgentSessionPrivacyRows, org_id=org_id) as session:
            try:
                landed = (await session.execute(written)).scalar_one_or_none() is not None
            except IntegrityError as error:
                await session.rollback()
                raise TenantMismatch(f"session privacy {record.id} is not in {org_id}") from error
            marked = rowcount(await session.execute(mark)) == 1
            row = (
                await session.execute(self._record(org_id, record.agent_session_id))
            ).scalar_one()
            stored = to_model(row, AgentSessionPrivacy)
            await session.execute(destroy)
            if landed or marked:
                for outbox_row in outbox_rows:
                    session.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            await session.commit()
            return stored

    async def rewrap_key(self, org_id: UUID, key: AgentSessionKey, expected: bytes) -> bool:
        stmt = (
            update(AgentSessionKeys)
            .where(
                AgentSessionKeys.org_id == org_id,
                AgentSessionKeys.id == key.id,
                AgentSessionKeys.wrapped == expected,
                AgentSessionKeys.destroyed_at.is_(None),
            )
            .values(wrapped=key.wrapped, wrapping=key.wrapping, wrapped_at=key.wrapped_at)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            changed = rowcount(await session.execute(stmt))
            await session.commit()
            return changed == 1

    async def read_keys_wrapped_before(
        self, org_id: UUID, before: datetime, limit: int
    ) -> list[AgentSessionKey]:
        stmt = (
            select(AgentSessionKeys)
            .where(
                AgentSessionKeys.org_id == org_id,
                AgentSessionKeys.destroyed_at.is_(None),
                AgentSessionKeys.wrapped_at < before,
            )
            .order_by(AgentSessionKeys.wrapped_at, AgentSessionKeys.id)
            .limit(limit)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            return [
                to_model(row, AgentSessionKey) for row in (await session.execute(stmt)).scalars()
            ]

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """The versions first, so a record never goes while a version of its
        key is left."""
        gone = 0
        for table in (AgentSessionKeys, AgentSessionPrivacyRows):
            if gone >= limit:
                break
            stmt = delete_batch(table, table.org_id == org_id, limit=limit - gone)
            async with self._session_for(stmt, org_id=org_id) as session:
                gone += deleted(await session.execute(stmt))
                await session.commit()
        return gone
