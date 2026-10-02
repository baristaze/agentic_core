"""The privacy storage contract: a session's record, written once; the
versions of its key, each once and never after the key is revoked; the
revocation that destroys every version and keeps every row; and the
rotation's compare-and-set. The cases named in `CROSS_TENANT_CASES` are the
tenant fence's evidence: each one presents another tenant's identifier and
asserts that nothing is found and nothing changes."""

import os
from datetime import timedelta
from uuid import UUID

import pytest

from acme.om.base import new_id, utcnow
from acme.om.exceptions import KeyRevoked, NotFound, TenantMismatch
from acme.om.privacy.storage import PrivacyStorageInterface
from acme.om.privacy.types.session_privacy import (
    SessionKey,
    SessionPrivacy,
    StorageMode,
    StoragePolicy,
)

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {
        "add_key",
        "create_privacy",
        "purge_tenant",
        "read_keys",
        "read_keys_wrapped_before",
        "read_privacy",
        "revoke",
        "rewrap_key",
    }
)
"""Every method of `PrivacyStorageInterface` that takes a tenant has a case
in this module that presents another tenant's."""

MEMORY_ONLY = StoragePolicy(mode=StorageMode.MEMORY_ONLY, keep_shape=False)


def make_record(session_id: UUID, policy: StoragePolicy | None = None) -> SessionPrivacy:
    return SessionPrivacy(
        id=new_id(), session_id=session_id, created_at=utcnow(), policy=policy or StoragePolicy()
    )


def make_key(
    session_id: UUID, version: int, wrapped_at_ago: timedelta = timedelta(0)
) -> SessionKey:
    now = utcnow()
    return SessionKey(
        id=new_id(),
        created_at=now,
        session_id=session_id,
        version=version,
        wrapped=os.urandom(48),
        wrapping="memory:1",
        wrapped_at=now - wrapped_at_ago,
    )


def revocation(session_id: UUID) -> SessionPrivacy:
    now = utcnow()
    return SessionPrivacy(
        id=new_id(), session_id=session_id, created_at=now, revoked_at=now, revoked_by=new_id()
    )


class PrivacyStorageContract:
    @pytest.fixture
    def storage(self) -> PrivacyStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    async def test_a_record_is_written_once(self, storage: PrivacyStorageInterface) -> None:
        org, session = new_id(), new_id()
        first = make_record(session, MEMORY_ONLY)
        assert await storage.create_privacy(org, first) == first
        assert await storage.create_privacy(org, make_record(session)) == first
        assert await storage.read_privacy(org, session) == first
        assert await storage.read_privacy(org, new_id()) is None

    async def test_a_version_is_kept_once_in_order_under_its_record(
        self, storage: PrivacyStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        with pytest.raises(NotFound):
            await storage.add_key(org, make_key(session, 1))
        await storage.create_privacy(org, make_record(session))
        second = await storage.add_key(org, make_key(session, 2))
        first = await storage.add_key(org, make_key(session, 1))
        assert await storage.add_key(org, make_key(session, 1)) == first
        ring = await storage.read_keys(org, session)
        assert not ring.revoked
        assert ring.keys == (first, second)
        assert ring.current() == second

    async def test_a_revocation_destroys_every_version_and_keeps_the_rows(
        self, storage: PrivacyStorageInterface
    ) -> None:
        """What a revoked key leaves: every version's row, with no wrapped
        copy left in any, and a record that refuses a version from then on.
        A second revocation changes nothing."""
        org, session, other = new_id(), new_id(), new_id()
        await storage.create_privacy(org, make_record(session))
        await storage.create_privacy(org, make_record(other))
        made = [await storage.add_key(org, make_key(session, n)) for n in (1, 2)]
        kept = await storage.add_key(org, make_key(other, 1))
        revoked = revocation(session)
        stored = await storage.revoke(org, revoked)
        assert (stored.revoked_at, stored.revoked_by) == (revoked.revoked_at, revoked.revoked_by)
        ring = await storage.read_keys(org, session)
        assert ring.revoked and ring.current() is None
        assert [key.id for key in ring.keys] == [key.id for key in made]
        assert all(key.wrapped is None and key.wrapping is None for key in ring.keys)
        assert all(key.destroyed_at == revoked.revoked_at for key in ring.keys)
        with pytest.raises(KeyRevoked):
            await storage.add_key(org, make_key(session, 3))
        again = await storage.revoke(org, revocation(session))
        assert again.revoked_at == revoked.revoked_at
        assert (await storage.read_keys(org, other)).keys == (kept,)

    async def test_a_session_with_no_record_is_revoked_all_the_same(
        self, storage: PrivacyStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        revoked = revocation(session)
        assert await storage.revoke(org, revoked) == revoked
        assert await storage.read_privacy(org, session) == revoked
        with pytest.raises(KeyRevoked):
            await storage.add_key(org, make_key(session, 1))

    async def test_a_rewrap_lands_only_over_the_copy_it_read(
        self, storage: PrivacyStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        await storage.create_privacy(org, make_record(session))
        key = await storage.add_key(org, make_key(session, 1))
        assert key.wrapped is not None
        moved = key.model_copy(update={"wrapped": os.urandom(48), "wrapping": "memory:2"})
        assert await storage.rewrap_key(org, moved, key.wrapped)
        assert not await storage.rewrap_key(org, moved, key.wrapped)
        assert (await storage.read_keys(org, session)).keys == (moved,)
        await storage.revoke(org, revocation(session))
        assert moved.wrapped is not None
        again = moved.model_copy(update={"wrapped": os.urandom(48)})
        assert not await storage.rewrap_key(org, again, moved.wrapped)
        assert (await storage.read_keys(org, session)).keys[0].wrapped is None

    async def test_the_versions_wrapped_before_a_time_come_longest_first(
        self, storage: PrivacyStorageInterface
    ) -> None:
        org, session, gone = new_id(), new_id(), new_id()
        for each in (session, gone):
            await storage.create_privacy(org, make_record(each))
        old = await storage.add_key(org, make_key(session, 1, timedelta(days=3)))
        older = await storage.add_key(org, make_key(session, 2, timedelta(days=5)))
        await storage.add_key(org, make_key(session, 3))
        await storage.add_key(org, make_key(gone, 1, timedelta(days=9)))
        await storage.revoke(org, revocation(gone))
        before = utcnow() - timedelta(days=1)
        assert await storage.read_keys_wrapped_before(org, before, 10) == [older, old]
        assert await storage.read_keys_wrapped_before(org, before, 1) == [older]

    async def test_another_tenant_naming_the_session_reaches_none_of_it(
        self, storage: PrivacyStorageInterface
    ) -> None:
        """A session's record and keys are its tenant's: another tenant that
        names the session reads nothing, adds no version to it, and a
        revocation it makes is its own, never the holder's. A record that
        presents the holder's id is refused."""
        org_a, org_b, session = new_id(), new_id(), new_id()
        record = make_record(session)
        await storage.create_privacy(org_a, record)
        key = await storage.add_key(org_a, make_key(session, 1, timedelta(days=2)))
        assert key.wrapped is not None
        assert await storage.read_privacy(org_b, session) is None
        assert (await storage.read_keys(org_b, session)).keys == ()
        with pytest.raises(NotFound):
            await storage.add_key(org_b, make_key(session, 2))
        with pytest.raises(TenantMismatch):
            await storage.create_privacy(org_b, record.model_copy(update={"policy": MEMORY_ONLY}))
        stolen = key.model_copy(update={"wrapped": os.urandom(48)})
        assert not await storage.rewrap_key(org_b, stolen, key.wrapped)
        assert await storage.read_keys_wrapped_before(org_b, utcnow(), 10) == []
        assert await storage.purge_tenant(org_b, 10) == 0
        own = await storage.revoke(org_b, revocation(session))
        assert own.revoked_at is not None
        assert await storage.read_privacy(org_a, session) == record
        ring = await storage.read_keys(org_a, session)
        assert not ring.revoked and ring.keys == (key,)

    async def test_a_tenants_purge_takes_its_rows_alone_versions_first(
        self, storage: PrivacyStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        for tenant in (org, other):
            for _ in range(2):
                session = new_id()
                await storage.create_privacy(tenant, make_record(session))
                await storage.add_key(tenant, make_key(session, 1))
        assert await storage.purge_tenant(org, 3) == 3
        assert await storage.purge_tenant(org, 3) == 1
        assert await storage.purge_tenant(org, 3) == 0
        assert await storage.read_keys_wrapped_before(org, utcnow(), 10) == []
        assert len(await storage.read_keys_wrapped_before(other, utcnow(), 10)) == 2
