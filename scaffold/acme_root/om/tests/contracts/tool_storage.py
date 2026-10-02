"""The tool storage contract: each tenant's layer of policy. The cases named
in `CROSS_TENANT_CASES` are the tenant fence's evidence: each one presents
another tenant's identifier and asserts that nothing is found and nothing
changes."""

import pytest

from acme.om.base import new_id, utcnow
from acme.om.context import Role
from acme.om.exceptions import PreconditionFailed, UniqueKeyTaken
from acme.om.tools.storage import ToolStorageInterface
from acme.om.tools.types.policy import ApproverRule, Decision, PolicyRule, ToolPolicy
from acme.om.tools.types.tool import Effect

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {"create_policy", "purge_tenant", "read_policy", "write_policy"}
)
"""Every method of `ToolStorageInterface` that takes a tenant has a case in
this module that presents another tenant's."""


def make_policy() -> ToolPolicy:
    now = utcnow()
    actor = new_id()
    return ToolPolicy(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=actor,
        updated_by=actor,
        rules=(
            PolicyRule(tool="push_branch", target={"protected": True}, decision=Decision.DENY),
            PolicyRule(
                authorization_class="read", effect=Effect.READ_ONLY, decision=Decision.ALLOW
            ),
        ),
        approvers=(ApproverRule(authorization_class="execute", roles=(Role.MEMBER, Role.ADMIN)),),
    )


class ToolStorageContract:
    @pytest.fixture
    def storage(self) -> ToolStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    async def test_round_trip(self, storage: ToolStorageInterface) -> None:
        org = new_id()
        assert await storage.read_policy(org) is None
        policy = make_policy()
        assert await storage.create_policy(org, policy, ())
        assert await storage.read_policy(org) == policy

    async def test_create_reports_an_existing_id_and_changes_nothing(
        self, storage: ToolStorageInterface
    ) -> None:
        org = new_id()
        policy = make_policy()
        assert await storage.create_policy(org, policy, ())
        assert not await storage.create_policy(org, policy.model_copy(update={"rules": ()}), ())
        assert await storage.read_policy(org) == policy

    async def test_a_tenant_holds_one_policy(self, storage: ToolStorageInterface) -> None:
        org = new_id()
        first = make_policy()
        assert await storage.create_policy(org, first, ())
        with pytest.raises(UniqueKeyTaken):
            await storage.create_policy(org, make_policy(), ())
        assert await storage.read_policy(org) == first

    async def test_create_policy_under_another_tenant_is_not_read_here(
        self, storage: ToolStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        policy = make_policy()
        assert await storage.create_policy(org_a, policy, ())
        assert not await storage.create_policy(org_b, policy, ())
        assert await storage.read_policy(org_b) is None

    async def test_read_policy_of_another_tenant_finds_nothing(
        self, storage: ToolStorageInterface
    ) -> None:
        assert await storage.create_policy(new_id(), make_policy(), ())
        assert await storage.read_policy(new_id()) is None

    async def test_write_policy_is_a_compare_and_set(self, storage: ToolStorageInterface) -> None:
        org = new_id()
        policy = make_policy()
        assert await storage.create_policy(org, policy, ())
        moved = policy.model_copy(update={"rules": (), "version": 2})
        await storage.write_policy(org, moved, 1, ())
        assert await storage.read_policy(org) == moved
        with pytest.raises(PreconditionFailed):
            await storage.write_policy(org, moved.model_copy(update={"version": 3}), 1, ())
        assert await storage.read_policy(org) == moved

    async def test_write_policy_of_another_tenant_changes_nothing(
        self, storage: ToolStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        policy = make_policy()
        assert await storage.create_policy(org_a, policy, ())
        with pytest.raises(PreconditionFailed):
            await storage.write_policy(org_b, policy.model_copy(update={"version": 2}), 1, ())
        assert await storage.read_policy(org_a) == policy

    async def test_purge_tenant_takes_the_tenants_policy_and_no_other(
        self, storage: ToolStorageInterface
    ) -> None:
        gone, kept = new_id(), new_id()
        stays = make_policy()
        assert await storage.create_policy(gone, make_policy(), ())
        assert await storage.create_policy(kept, stays, ())
        assert await storage.purge_tenant(gone, 10) == 1
        assert await storage.purge_tenant(gone, 10) == 0
        assert await storage.read_policy(gone) is None
        assert await storage.read_policy(kept) == stays
