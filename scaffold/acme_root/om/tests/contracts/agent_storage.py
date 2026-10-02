"""The agent tree storage contract: the record a tree's sessions share, the
slot a spawn takes in one conditional write, and the compare-and-set of
every other write. The cases named in `CROSS_TENANT_CASES` are the tenant
fence's evidence: each one presents another tenant's identifier and
asserts that nothing is found and nothing changes."""

from datetime import timedelta

import pytest

from acme.om.agents.storage import AgentStorageInterface
from acme.om.agents.types.tree import AgentTree
from acme.om.base import new_id, utcnow
from acme.om.exceptions import PreconditionFailed
from contracts.racing import race

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {"create_tree", "purge_tenant", "purge_tree", "read_tree", "take_slot", "write_tree"}
)
"""Every method of `AgentStorageInterface` that takes a tenant has a case
in this module that presents another tenant's."""


def make_tree(count: int = 3) -> AgentTree:
    now = utcnow()
    by = new_id()
    return AgentTree(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=by,
        updated_by=by,
        height=2,
        count=count,
        concurrency=2,
        deadline=now + timedelta(hours=4),
    )


def moved(tree: AgentTree, version: int) -> AgentTree:
    """The tree as a person leaves it when they move its deadline."""
    later = tree.deadline + timedelta(hours=1) if tree.deadline else None
    return tree.model_copy(update={"deadline": later, "version": version, "updated_at": utcnow()})


class AgentStorageContract:
    @pytest.fixture
    def storage(self) -> AgentStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    async def test_round_trip(self, storage: AgentStorageInterface) -> None:
        org = new_id()
        tree = make_tree()
        assert await storage.create_tree(org, tree, ())
        assert await storage.read_tree(org, tree.id) == tree
        assert await storage.read_tree(org, new_id()) is None

    async def test_create_reports_an_existing_id_and_changes_nothing(
        self, storage: AgentStorageInterface
    ) -> None:
        org = new_id()
        tree = make_tree()
        assert await storage.create_tree(org, tree, ())
        assert not await storage.create_tree(org, tree.model_copy(update={"height": 9}), ())
        assert await storage.read_tree(org, tree.id) == tree

    async def test_create_tree_under_another_tenant_is_not_read_here(
        self, storage: AgentStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        tree = make_tree()
        assert await storage.create_tree(org_a, tree, ())
        assert not await storage.create_tree(org_b, tree.model_copy(update={"height": 9}), ())
        assert await storage.read_tree(org_b, tree.id) is None
        assert await storage.read_tree(org_a, tree.id) == tree

    async def test_slots_are_taken_up_to_the_count_and_no_further(
        self, storage: AgentStorageInterface
    ) -> None:
        org = new_id()
        tree = make_tree(count=2)
        assert await storage.create_tree(org, tree, ())
        first = await storage.take_slot(org, tree.id)
        second = await storage.take_slot(org, tree.id)
        assert first is not None and (first.size, first.version) == (1, 2)
        assert second is not None and (second.size, second.version) == (2, 3)
        assert await storage.take_slot(org, tree.id) is None
        assert await storage.read_tree(org, tree.id) == second
        assert await storage.take_slot(org, new_id()) is None

    async def test_spawns_at_once_never_pass_the_count(
        self, storage: AgentStorageInterface
    ) -> None:
        # Eight spawns reach a tree with room for three: three slots, and the
        # size stops at the count. See contracts/racing.py for what each
        # impl's run of this proves.
        org = new_id()
        tree = make_tree(count=3)
        assert await storage.create_tree(org, tree, ())
        run = await race(*(storage.take_slot(org, tree.id) for _ in range(8)))
        assert len(run.admitted) == 3, run.summary()
        assert sorted(t.size for t in run.admitted if t is not None) == [1, 2, 3]
        stored = await storage.read_tree(org, tree.id)
        assert stored is not None and stored.size == 3

    async def test_take_slot_under_another_tenant_takes_nothing(
        self, storage: AgentStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        tree = make_tree()
        assert await storage.create_tree(org_a, tree, ())
        assert await storage.take_slot(org_b, tree.id) is None
        assert await storage.read_tree(org_a, tree.id) == tree

    async def test_write_is_a_compare_and_set_and_a_slot_moves_the_version(
        self, storage: AgentStorageInterface
    ) -> None:
        org = new_id()
        tree = make_tree()
        assert await storage.create_tree(org, tree, ())
        later = moved(tree, version=2)
        await storage.write_tree(org, later, 1, ())
        assert await storage.read_tree(org, tree.id) == later
        taken = await storage.take_slot(org, tree.id)
        assert taken is not None and taken.version == 3
        # A write from the snapshot before the slot would take the slot back.
        with pytest.raises(PreconditionFailed):
            await storage.write_tree(org, moved(later, version=3), 2, ())
        assert await storage.read_tree(org, tree.id) == taken

    async def test_write_tree_under_another_tenant_lands_nothing(
        self, storage: AgentStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        tree = make_tree()
        assert await storage.create_tree(org_a, tree, ())
        with pytest.raises(PreconditionFailed):
            await storage.write_tree(org_b, moved(tree, version=2), 1, ())
        assert await storage.read_tree(org_a, tree.id) == tree

    async def test_purge_tree_takes_one_tree_and_no_other(
        self, storage: AgentStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        purged, kept = make_tree(), make_tree()
        for tree in (purged, kept):
            assert await storage.create_tree(org, tree, ())
        assert not await storage.purge_tree(other, purged.id), "another tenant's"
        assert await storage.read_tree(org, purged.id) == purged
        assert await storage.purge_tree(org, purged.id)
        assert await storage.read_tree(org, purged.id) is None
        assert not await storage.purge_tree(org, purged.id), "gone already"
        assert await storage.read_tree(org, kept.id) == kept

    async def test_purge_tenant_takes_the_tenants_trees_a_batch_at_a_time(
        self, storage: AgentStorageInterface
    ) -> None:
        gone, kept = new_id(), new_id()
        trees = [make_tree() for _ in range(3)]
        for tree in trees:
            assert await storage.create_tree(gone, tree, ())
        stays = make_tree()
        assert await storage.create_tree(kept, stays, ())
        assert await storage.purge_tenant(gone, 2) == 2
        assert await storage.purge_tenant(gone, 2) == 1
        assert await storage.purge_tenant(gone, 2) == 0
        assert [await storage.read_tree(gone, tree.id) for tree in trees] == [None] * 3
        assert await storage.read_tree(kept, stays.id) == stays
