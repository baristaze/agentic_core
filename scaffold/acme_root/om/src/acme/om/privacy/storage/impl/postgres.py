from collections.abc import Collection
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
from acme.om.privacy.storage.tables.tenant_title_keys import TenantTitleKeys
from acme.om.privacy.types.agent_session_privacy import (
    AgentSessionKey,
    AgentSessionPrivacy,
    KeyRing,
    TenantTitleKey,
    TitleKeys,
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

    async def choose_policy(
        self, org_id: UUID, record: AgentSessionPrivacy, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> tuple[AgentSessionPrivacy, bool]:
        """The insert, or else the take of a record none chose: its WHERE
        holds no choice and no revocation, so a revocation or another choice
        that landed first keeps the record as it is."""
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
        take = (
            update(AgentSessionPrivacyRows)
            .where(
                AgentSessionPrivacyRows.org_id == org_id,
                AgentSessionPrivacyRows.agent_session_id == record.agent_session_id,
                AgentSessionPrivacyRows.revoked_at.is_(None),
                AgentSessionPrivacyRows.chosen.is_(False),
            )
            .values(policy=record.policy.model_dump(mode="json"), chosen=True)
        )
        async with self._session_for(AgentSessionPrivacyRows, org_id=org_id) as session:
            try:
                landed = (await session.execute(insert)).scalar_one_or_none() is not None
            except IntegrityError as error:
                await session.rollback()
                raise TenantMismatch(f"session privacy {record.id} is not in {org_id}") from error
            if not landed:
                landed = rowcount(await session.execute(take)) == 1
            if landed:
                for outbox_row in outbox_rows:
                    session.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            row = (
                await session.execute(self._record(org_id, record.agent_session_id))
            ).scalar_one()
            stored = to_model(row, AgentSessionPrivacy)
            await session.commit()
            return stored, landed

    async def add_title_key(self, org_id: UUID, record: AgentSessionPrivacy) -> AgentSessionPrivacy:
        """The insert, or else the record's row lock, as the revocation takes
        it, and the key set when it holds none and is not revoked: a key and
        a revocation of one session queue on the row, and a key never lands
        after the revocation."""
        if record.title_key is None:
            raise ValueError("a title key is given to be added")
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
        locked = self._record(org_id, record.agent_session_id).with_for_update()
        async with self._session_for(AgentSessionPrivacyRows, org_id=org_id) as session:
            try:
                landed = (await session.execute(insert)).scalar_one_or_none() is not None
            except IntegrityError as error:
                await session.rollback()
                raise TenantMismatch(f"session privacy {record.id} is not in {org_id}") from error
            if landed:
                await session.commit()
                return record
            row = (await session.execute(locked)).scalar_one()
            if row.revoked_at is None and row.title_key is None:
                row.title_key = record.title_key
                await session.flush()
            stored = to_model(row, AgentSessionPrivacy)
            await session.commit()
            return stored

    async def read_title_keys(self, org_id: UUID, agent_session_ids: Collection[UUID]) -> TitleKeys:
        """The tenant's key, joined to each named record that holds a title
        key: no key, no row, and no title opens."""
        stmt = (
            select(
                TenantTitleKeys,
                AgentSessionPrivacyRows.agent_session_id,
                AgentSessionPrivacyRows.title_key,
            )
            .select_from(TenantTitleKeys)
            .outerjoin(
                AgentSessionPrivacyRows,
                and_(
                    AgentSessionPrivacyRows.org_id == TenantTitleKeys.org_id,
                    AgentSessionPrivacyRows.agent_session_id.in_(list(agent_session_ids)),
                    AgentSessionPrivacyRows.title_key.is_not(None),
                    AgentSessionPrivacyRows.revoked_at.is_(None),
                ),
            )
            .where(TenantTitleKeys.org_id == org_id)
        )
        async with self._session_for(TenantTitleKeys, org_id=org_id) as session:
            rows = (await session.execute(stmt)).all()
        if not rows:
            return TitleKeys()
        return TitleKeys(
            tenant=to_model(rows[0].TenantTitleKeys, TenantTitleKey),
            sessions=tuple(
                (row.agent_session_id, row.title_key) for row in rows if row.title_key is not None
            ),
        )

    async def add_tenant_title_key(self, org_id: UUID, key: TenantTitleKey) -> TenantTitleKey:
        insert = (
            pg_insert(TenantTitleKeys)
            .values(org_id=org_id, **to_values(key, TenantTitleKeys))
            .on_conflict_do_nothing(index_elements=[TenantTitleKeys.org_id])
            .returning(TenantTitleKeys.id)
        )
        async with self._session_for(TenantTitleKeys, org_id=org_id) as session:
            try:
                landed = (await session.execute(insert)).scalar_one_or_none() is not None
            except IntegrityError as error:
                await session.rollback()
                raise TenantMismatch(f"tenant title key {key.id} is not in {org_id}") from error
            if landed:
                await session.commit()
                return key
            row = (
                await session.execute(
                    select(TenantTitleKeys).where(TenantTitleKeys.org_id == org_id)
                )
            ).scalar_one()
            await session.commit()
            return to_model(row, TenantTitleKey)

    async def rewrap_tenant_title_key(
        self, org_id: UUID, key: TenantTitleKey, expected: bytes
    ) -> bool:
        stmt = (
            update(TenantTitleKeys)
            .where(
                TenantTitleKeys.org_id == org_id,
                TenantTitleKeys.id == key.id,
                TenantTitleKeys.wrapped == expected,
            )
            .values(wrapped=key.wrapped, wrapping=key.wrapping, wrapped_at=key.wrapped_at)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            changed = rowcount(await session.execute(stmt))
            await session.commit()
            return changed == 1

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
        # On every call, so a revocation a release before titles were sealed
        # made, which left the title key, is finished by the next.
        clear = (
            update(AgentSessionPrivacyRows)
            .where(
                AgentSessionPrivacyRows.org_id == org_id,
                AgentSessionPrivacyRows.agent_session_id == record.agent_session_id,
                AgentSessionPrivacyRows.title_key.is_not(None),
            )
            .values(title_key=None)
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
            await session.execute(clear)
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
        key is left, and the tenant's title key last, so it never goes while
        a title key it wraps is left."""
        gone = 0
        for table in (AgentSessionKeys, AgentSessionPrivacyRows, TenantTitleKeys):
            if gone >= limit:
                break
            stmt = delete_batch(table, table.org_id == org_id, limit=limit - gone)
            async with self._session_for(stmt, org_id=org_id) as session:
                gone += deleted(await session.execute(stmt))
                await session.commit()
        return gone
