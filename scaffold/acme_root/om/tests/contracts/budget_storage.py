"""The budget storage contract. The cases named in `CROSS_TENANT_CASES` are
the tenant fence's evidence: each one presents another tenant's identifier
and asserts that nothing is found and nothing changes."""

import pytest

from acme.om.base import new_id, utcnow
from acme.om.budgets.storage import BudgetStorageInterface
from acme.om.budgets.types.budget import Budget, BudgetScope, BudgetScopeKind, WindowKind
from acme.om.exceptions import PreconditionFailed

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {
        "create_budget",
        "purge_tenant",
        "read_budget",
        "read_budgets",
        "read_budgets_for",
        "write_budget",
    }
)
"""Every method of `BudgetStorageInterface` that takes a tenant has a case in
this module that presents another tenant's."""


def make_budget(
    kind: BudgetScopeKind = BudgetScopeKind.SESSION,
    key: str | None = None,
    *,
    window: WindowKind = WindowKind.DAY,
    seconds: int | None = None,
    cost_micros: int | None = 1_000_000,
    tokens: int | None = None,
) -> Budget:
    now = utcnow()
    actor = new_id()
    return Budget(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=actor,
        updated_by=actor,
        scope_kind=kind,
        scope_key=key or str(new_id()),
        window_kind=window,
        window_seconds=seconds,
        cost_micros=cost_micros,
        tokens=tokens,
    )


def raised(budget: Budget, cost_micros: int, version: int) -> Budget:
    return budget.model_copy(
        update={"cost_micros": cost_micros, "version": version, "updated_at": utcnow()}
    )


class BudgetStorageContract:
    @pytest.fixture
    def storage(self) -> BudgetStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    async def test_round_trip(self, storage: BudgetStorageInterface) -> None:
        org = new_id()
        budget = make_budget(tokens=500, window=WindowKind.SPAN, seconds=90)
        assert await storage.create_budget(org, budget, ())
        assert await storage.read_budget(org, budget.id) == budget
        assert await storage.read_budget(org, new_id()) is None

    async def test_create_reports_an_existing_id_and_changes_nothing(
        self, storage: BudgetStorageInterface
    ) -> None:
        org = new_id()
        budget = make_budget()
        assert await storage.create_budget(org, budget, ())
        assert not await storage.create_budget(org, raised(budget, 5, 1), ())
        assert await storage.read_budget(org, budget.id) == budget

    async def test_create_budget_under_another_tenant_is_not_read_here(
        self, storage: BudgetStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        budget = make_budget()
        assert await storage.create_budget(org_a, budget, ())
        assert not await storage.create_budget(org_b, raised(budget, 5, 1), ())
        assert await storage.read_budget(org_b, budget.id) is None
        assert await storage.read_budget(org_a, budget.id) == budget

    async def test_read_budgets_pages_by_id_within_the_tenant(
        self, storage: BudgetStorageInterface
    ) -> None:
        org, elsewhere = new_id(), new_id()
        budgets = sorted((make_budget() for _ in range(3)), key=lambda b: b.id)
        for budget in budgets:
            assert await storage.create_budget(org, budget, ())
        assert await storage.create_budget(elsewhere, make_budget(), ())
        assert await storage.read_budgets(org, None, 10) == budgets
        assert await storage.read_budgets(org, budgets[0].id, 1) == [budgets[1]]
        assert await storage.read_budgets(new_id(), None, 10) == []

    async def test_read_budgets_for_takes_the_scopes_named_and_no_other(
        self, storage: BudgetStorageInterface
    ) -> None:
        org, elsewhere = new_id(), new_id()
        session, person = str(new_id()), str(new_id())
        day = make_budget(BudgetScopeKind.SESSION, session)
        month = make_budget(BudgetScopeKind.SESSION, session, window=WindowKind.MONTH)
        own = make_budget(BudgetScopeKind.PERSON, person, tokens=10, cost_micros=None)
        # The same key under another kind is another scope.
        stranger = make_budget(BudgetScopeKind.PROJECT, session)
        for budget in (day, month, own, stranger):
            assert await storage.create_budget(org, budget, ())
        assert await storage.create_budget(
            elsewhere, make_budget(BudgetScopeKind.SESSION, session), ()
        )
        scopes = [
            BudgetScope(kind=BudgetScopeKind.SESSION, key=session),
            BudgetScope(kind=BudgetScopeKind.PERSON, key=person),
        ]
        found = await storage.read_budgets_for(org, scopes, 10)
        assert found == sorted([day, month, own], key=lambda b: b.id)
        assert len(await storage.read_budgets_for(org, scopes, 2)) == 2
        assert await storage.read_budgets_for(org, [], 10) == []
        assert await storage.read_budgets_for(new_id(), scopes, 10) == []

    async def test_write_is_a_compare_and_set_on_the_version(
        self, storage: BudgetStorageInterface
    ) -> None:
        org = new_id()
        budget = make_budget()
        assert await storage.create_budget(org, budget, ())
        moved = raised(budget, 2_000_000, 2)
        await storage.write_budget(org, moved, 1, ())
        assert await storage.read_budget(org, budget.id) == moved
        with pytest.raises(PreconditionFailed):
            await storage.write_budget(org, raised(budget, 3_000_000, 2), 1, ())
        assert await storage.read_budget(org, budget.id) == moved

    async def test_write_budget_under_another_tenant_lands_nothing(
        self, storage: BudgetStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        budget = make_budget()
        assert await storage.create_budget(org_a, budget, ())
        with pytest.raises(PreconditionFailed):
            await storage.write_budget(org_b, raised(budget, 9, 2), 1, ())
        assert await storage.read_budget(org_a, budget.id) == budget

    async def test_purge_tenant_takes_a_batch_of_the_tenants_budgets_and_no_other(
        self, storage: BudgetStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        for _ in range(3):
            assert await storage.create_budget(org_a, make_budget(), ())
        kept = make_budget()
        assert await storage.create_budget(org_b, kept, ())
        assert await storage.purge_tenant(org_a, 2) == 2
        assert await storage.purge_tenant(org_a, 2) == 1
        assert await storage.read_budgets(org_a, None, 10) == []
        assert await storage.read_budgets(org_b, None, 10) == [kept]
