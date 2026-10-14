"""The ledger contract over Postgres, and what only the database holds: two
sessions at the gate at once over one line queue on its tally, and the
serving logins cannot rewrite or remove a hold or a settlement."""

from uuid import UUID

import pytest
from contracts.doubles import context
from contracts.ledger_storage import LedgerStorageContract, a_hold, a_line
from contracts.racing import race
from contracts.spend_budget_storage import make_spend_budget
from sqlalchemy import TextClause, text
from sqlalchemy.exc import DBAPIError

from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.context import Role
from acme.om.spend_budgets.impl.gate import SpendBudgetGateImpl, SpendBudgetGateOptions
from acme.om.spend_budgets.rules import spend_settlement_of, window_bounds
from acme.om.spend_budgets.storage import LedgerStorageInterface
from acme.om.spend_budgets.storage.impl.postgres import (
    LedgerStoragePostgresImpl,
    SpendBudgetStoragePostgresImpl,
)
from acme.om.spend_budgets.types.amount import Spend
from acme.om.spend_budgets.types.spend_budget import SpendBudgetScope, SpendBudgetScopeKind
from acme.om.spend_budgets.types.spend_hold import BillUnknown, SpendHold, SpendHoldRequest
from acme.om.storage.impl.pg_base import LoginSessions, SessionFactory, set_scope
from acme.om.storage.logins import RUNTIME_LOGIN, SYSTEM_LOGIN
from acme.om.storage.migrate import ensure_logins_everywhere
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import MigrationSettings

pytestmark = pytest.mark.integration


class TestLedgerStoragePostgres(LedgerStorageContract):
    @pytest.fixture
    def storage(self, pg_sessions: LoginSessions) -> LedgerStorageInterface:
        return LedgerStoragePostgresImpl(pg_sessions)

    async def test_holds_over_one_line_meet_on_its_tally_and_only_those_that_fit_pass(
        self, storage: LedgerStorageInterface
    ) -> None:
        """The contract's race, held to have raced: over the engine the opens
        are inside their transactions at once, and the tally's lock lets
        exactly as many through as fit."""
        org = new_id()
        line = a_line(cost_micros=3_000)

        async def one() -> SpendHold | None:
            hold = a_hold(line, cost_micros=1_000)
            return hold if await storage.open_spend_hold(org, hold) is None else None

        run = await race(*(one() for _ in range(12)))
        assert run.overlapped, run.summary()
        assert len(run.admitted) == 3, run.summary()
        tally = await storage.read_spend_tally(org, line.spend_budget_id, line.window_start)
        assert tally is not None and tally.held_cost_micros == 3_000


async def test_two_sessions_at_the_gate_at_once_never_both_pass_when_one_fits(
    pg_sessions: LoginSessions,
) -> None:
    """Two sessions of one tenant authorize at once against the tenant's one
    daily line, which has room for one call: one is held, the other refused
    with its breach, and the line holds one call's worst case."""
    budgets, ledger = (
        SpendBudgetStoragePostgresImpl(pg_sessions),
        LedgerStoragePostgresImpl(pg_sessions),
    )
    gate = SpendBudgetGateImpl(budgets, ledger, SpendBudgetGateOptions())
    ctx = context(Role.MEMBER)
    tenant = SpendBudgetScope(kind=SpendBudgetScopeKind.TENANT, key=str(ctx.org_id))
    line = make_spend_budget(tenant.kind, tenant.key, cost_micros=1_500)
    assert await budgets.create_spend_budget(ctx.org_id, line, ())

    def asked(agent_session_id: UUID) -> SpendHoldRequest:
        return SpendHoldRequest(
            spender_id=ctx.user_id,
            scopes=(
                tenant,
                SpendBudgetScope(
                    kind=SpendBudgetScopeKind.AGENT_SESSION, key=str(agent_session_id)
                ),
            ),
            exposure=Spend(cost_micros=1_000, tokens=4_000),
            agent_session_id=agent_session_id,
            purpose="main",
        )

    run = await race(gate.authorize(ctx, asked(new_id())), gate.authorize(ctx, asked(new_id())))
    assert run.overlapped, run.summary()
    holds = [answer for answer in run.outcomes if isinstance(answer, SpendHold)]
    refusals = [answer for answer in run.outcomes if not isinstance(answer, SpendHold)]
    assert (len(holds), len(refusals)) == (1, 1), run.summary()
    (breach,) = refusals[0].breaches
    assert (breach.spend_budget_id, breach.committed, breach.needed) == (line.id, 1_000, 2_000)
    start, _ = window_bounds(line.window, utcnow())
    tally = await ledger.read_spend_tally(ctx.org_id, line.id, start)
    assert tally is not None and tally.held_cost_micros == 1_000


async def refused(factory: SessionFactory, org_id: UUID, statement: TextClause) -> str:
    """The error a statement meets under the given scope, on a session of
    its own, since a refused statement ends its transaction."""
    async with factory() as session:
        await set_scope(session, org_id, None, None)
        with pytest.raises(DBAPIError) as error:
            await session.execute(statement)
        return str(error.value)


async def test_no_serving_login_rewrites_or_removes_a_hold_or_a_settlement(
    pg_sessions: LoginSessions, migration_settings: MigrationSettings
) -> None:
    """A hold and a settlement are written once: the serving logins may read
    and insert them and are refused an UPDATE and a DELETE by the database
    itself, after a deploy made the logins again. The tally beside them stays
    theirs to move."""
    settings = migration_settings
    await ensure_logins_everywhere(settings.master_databases(), settings.login_passwords())
    storage = LedgerStoragePostgresImpl(pg_sessions)
    org = new_id()
    line = a_line(cost_micros=10_000)
    hold = a_hold(line)
    assert await storage.open_spend_hold(org, hold) is None
    await storage.close_spend_hold(
        org, spend_settlement_of(hold, BillUnknown(), new_id(), utcnow())
    )

    for factory, scope in (
        (pg_sessions[DatabaseRole.ACTIVITY], org),
        (pg_sessions.system[DatabaseRole.ACTIVITY], EMPTY_UUID),
    ):
        for table in ("spend_budget_holds", "spend_budget_settlements"):
            for statement in (
                text(f"UPDATE activity.{table} SET created_at = created_at"),
                text(f"DELETE FROM activity.{table}"),
            ):
                error = await refused(factory, scope, statement)
                assert f"permission denied for table {table}" in error, error
        async with factory() as db:
            await set_scope(db, scope, None, None)
            moved = text(
                "UPDATE activity.spend_budget_tallies SET held_tokens = held_tokens RETURNING org_id"
            )
            assert (await db.execute(moved)).scalars().all() == [org]
            await db.rollback()

    privilege = text("SELECT has_table_privilege(:login, :table, :privilege)")
    async with pg_sessions[DatabaseRole.ACTIVITY]() as db:
        for table in ("activity.spend_budget_holds", "activity.spend_budget_settlements"):
            held: set[tuple[str, str]] = set()
            for login in (RUNTIME_LOGIN, SYSTEM_LOGIN):
                for kind in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                    found = await db.execute(
                        privilege, {"login": login, "table": table, "privilege": kind}
                    )
                    if found.scalar_one():
                        held.add((login, kind))
            assert held == {
                (RUNTIME_LOGIN, "SELECT"),
                (RUNTIME_LOGIN, "INSERT"),
                (SYSTEM_LOGIN, "SELECT"),
                (SYSTEM_LOGIN, "INSERT"),
            }, table
    assert await storage.read_hold(org, hold.id) == hold
