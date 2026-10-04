import logging
from uuid import UUID

from acme.om.base import Platform
from acme.om.budgets.manager import BudgetsOperatorManagerInterface
from acme.om.budgets.storage import LedgerStorageInterface
from acme.om.budgets.types.usage import SessionUsage
from acme.om.context import OperatorContext, OperatorPermission
from acme.om.exceptions import NotFound
from acme.om.tenancy.storage import TenancyStorageInterface

log = logging.getLogger(__name__)


class BudgetsOperatorOptions(Platform):
    max_limit: int = 200  # usage records one page holds at most
    max_loops: int = 1000  # loop rollups one read holds at most


class BudgetsOperatorManagerImpl(BudgetsOperatorManagerInterface):
    """Reads one named org's usage records through the ledger storage, under
    the tenant the operator named, and reads the org through the tenancy
    storage, as the other operator planes do: no `TenantContext` exists on
    this plane, so no tenant manager is asked."""

    def __init__(
        self,
        ledger: LedgerStorageInterface,
        tenancy: TenancyStorageInterface,
        options: BudgetsOperatorOptions,
    ) -> None:
        self._ledger = ledger
        self._tenancy = tenancy
        self._options = options

    async def get_session_usage(
        self,
        admin: OperatorContext,
        org_id: UUID,
        session_id: UUID,
        after: UUID | None,
        limit: int,
    ) -> SessionUsage:
        admin.require(OperatorPermission.READ)
        if await self._tenancy.read_org(org_id) is None:
            # A deleted org reads until the sweep takes it; its ledger stays.
            raise NotFound(f"org {org_id} not found")
        total = await self._ledger.read_usage_total(org_id, session_id)
        if total.calls == 0:
            raise NotFound(f"no usage of session {session_id} in org {org_id}")
        bound = max(1, min(limit, self._options.max_limit))
        records = await self._ledger.read_usage_records(org_id, session_id, after, bound + 1)
        loops = await self._ledger.read_usage_rollups(
            org_id, session_id, self._options.max_loops + 1
        )
        # The support trail: who read which tenant's usage, and nothing of it.
        log.info("operator %s read usage of org %s", admin.identity_id, org_id)
        return SessionUsage(
            session_id=session_id,
            records=tuple(records[:bound]),
            has_more=len(records) > bound,
            loops=tuple(loops[: self._options.max_loops]),
            has_more_loops=len(loops) > self._options.max_loops,
            total=total,
        )
