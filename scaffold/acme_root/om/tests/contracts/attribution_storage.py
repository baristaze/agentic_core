"""The attribution storage contract: one authority per session, made once
and written after by compare-and-set. The cases named in
`CROSS_TENANT_CASES` are the tenant fence's evidence: each one presents
another tenant's identifier and asserts that nothing is found and nothing
changes."""

import pytest

from acme.om.attribution.storage import AttributionStorageInterface
from acme.om.attribution.types.authority import AuthorityMode, SessionAuthority
from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.base import new_id, utcnow
from acme.om.exceptions import PreconditionFailed

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {"create_authority", "purge_authority", "purge_tenant", "read_authority", "write_authority"}
)
"""Every method of `AttributionStorageInterface` that takes a tenant has a
case in this module that presents another tenant's."""


def make_authority() -> SessionAuthority:
    now, by = utcnow(), new_id()
    return SessionAuthority(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=by,
        updated_by=by,
        mode=AuthorityMode.STEADY,
        principal=Principal(kind=PrincipalKind.PERSON, id=by),
        spender=Principal(kind=PrincipalKind.SERVICE, id=new_id()),
    )


def taken_over(authority: SessionAuthority, version: int) -> SessionAuthority:
    """The authority as a person leaves it when they take the session over."""
    principal = Principal(kind=PrincipalKind.PERSON, id=new_id())
    return authority.model_copy(
        update={"principal": principal, "version": version, "updated_at": utcnow()}
    )


class AttributionStorageContract:
    @pytest.fixture
    def storage(self) -> AttributionStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    async def test_round_trip(self, storage: AttributionStorageInterface) -> None:
        org = new_id()
        authority = make_authority()
        assert await storage.create_authority(org, authority, ())
        assert await storage.read_authority(org, authority.id) == authority
        assert await storage.read_authority(org, new_id()) is None

    async def test_create_reports_an_existing_id_and_changes_nothing(
        self, storage: AttributionStorageInterface
    ) -> None:
        org = new_id()
        authority = make_authority()
        assert await storage.create_authority(org, authority, ())
        assert not await storage.create_authority(org, taken_over(authority, 1), ())
        assert await storage.read_authority(org, authority.id) == authority

    async def test_create_authority_under_another_tenant_is_not_read_here(
        self, storage: AttributionStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        authority = make_authority()
        assert await storage.create_authority(org_a, authority, ())
        assert not await storage.create_authority(org_b, taken_over(authority, 1), ())
        assert await storage.read_authority(org_b, authority.id) is None
        assert await storage.read_authority(org_a, authority.id) == authority

    async def test_write_is_a_compare_and_set_on_the_version(
        self, storage: AttributionStorageInterface
    ) -> None:
        org = new_id()
        authority = make_authority()
        assert await storage.create_authority(org, authority, ())
        moved = taken_over(authority, version=2)
        await storage.write_authority(org, moved, 1, ())
        assert await storage.read_authority(org, authority.id) == moved
        with pytest.raises(PreconditionFailed):
            await storage.write_authority(org, taken_over(authority, version=2), 1, ())
        assert await storage.read_authority(org, authority.id) == moved

    async def test_write_authority_under_another_tenant_lands_nothing(
        self, storage: AttributionStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        authority = make_authority()
        assert await storage.create_authority(org_a, authority, ())
        with pytest.raises(PreconditionFailed):
            await storage.write_authority(org_b, taken_over(authority, version=2), 1, ())
        assert await storage.read_authority(org_a, authority.id) == authority

    async def test_purge_authority_takes_one_session_and_no_other(
        self, storage: AttributionStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        purged, kept = make_authority(), make_authority()
        for authority in (purged, kept):
            assert await storage.create_authority(org, authority, ())
        assert not await storage.purge_authority(other, purged.id), "another tenant's"
        assert await storage.read_authority(org, purged.id) == purged
        assert await storage.purge_authority(org, purged.id)
        assert await storage.read_authority(org, purged.id) is None
        assert not await storage.purge_authority(org, purged.id), "gone already"
        assert await storage.read_authority(org, kept.id) == kept

    async def test_purge_tenant_takes_the_tenants_authorities_a_batch_at_a_time(
        self, storage: AttributionStorageInterface
    ) -> None:
        gone, kept = new_id(), new_id()
        authorities = [make_authority() for _ in range(3)]
        for authority in authorities:
            assert await storage.create_authority(gone, authority, ())
        stays = make_authority()
        assert await storage.create_authority(kept, stays, ())
        assert await storage.purge_tenant(gone, 2) == 2
        assert await storage.purge_tenant(gone, 2) == 1
        assert await storage.purge_tenant(gone, 2) == 0
        assert [await storage.read_authority(gone, a.id) for a in authorities] == [None] * 3
        assert await storage.read_authority(kept, stays.id) == stays
