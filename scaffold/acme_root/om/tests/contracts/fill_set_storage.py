"""The fill-set storage contract: every version of a session's fill set,
each written once. The cases named in `CROSS_TENANT_CASES` are the tenant
fence's evidence: each one presents another tenant's identifier and asserts
that nothing is found and nothing changes."""

from uuid import UUID

import pytest

from acme.integrations.model_providers.types import Effort, ProviderName
from acme.om.base import new_id, utcnow
from acme.om.exceptions import PreconditionFailed
from acme.om.models.storage import FillSetStorageInterface
from acme.om.models.types.fill import (
    MAIN,
    SUMMARIZER,
    Eligibility,
    Fill,
    FillSet,
    RoleFill,
    SwitchReason,
)

CROSS_TENANT_CASES: frozenset[str] = frozenset({"read_fill_set", "write_fill_set"})
"""Every method of `FillSetStorageInterface` that takes a tenant has a case
in this module that presents another tenant's."""

SONNET = Fill(
    provider=ProviderName.ANTHROPIC,
    model="claude-sonnet-5-5",
    effort=Effort.HIGH,
    max_output_tokens=32_000,
    context_window=1_000_000,
)
SOL = Fill(
    provider=ProviderName.OPENAI,
    model="gpt-6.1-sol",
    effort=Effort.HIGH,
    max_output_tokens=32_000,
    context_window=1_050_000,
)
HAIKU = Fill(
    provider=ProviderName.ANTHROPIC,
    model="claude-haiku-4-5",
    max_output_tokens=8_000,
    context_window=200_000,
)


def make_fill_set(session_id: UUID, version: int = 1, main: Fill = SONNET) -> FillSet:
    switch = version > 1
    return FillSet(
        id=new_id(),
        created_at=utcnow(),
        session_id=session_id,
        version=version,
        roles=(
            RoleFill(role=MAIN, fill=main, fallbacks=(SOL,)),
            RoleFill(role=SUMMARIZER, fill=HAIKU),
        ),
        eligibility=Eligibility(region="eu") if not switch else Eligibility(),
        reason=SwitchReason.FALLBACK if switch else None,
        switched_by=new_id() if switch else None,
    )


class FillSetStorageContract:
    @pytest.fixture
    def storage(self) -> FillSetStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    async def test_round_trip(self, storage: FillSetStorageInterface) -> None:
        org, session = new_id(), new_id()
        first = make_fill_set(session)
        assert await storage.write_fill_set(org, first)
        assert await storage.read_fill_set(org, session, None) == first
        assert await storage.read_fill_set(org, session, 1) == first
        assert await storage.read_fill_set(org, session, 2) is None
        assert await storage.read_fill_set(org, new_id(), None) is None

    async def test_the_latest_version_is_the_head(self, storage: FillSetStorageInterface) -> None:
        org, session = new_id(), new_id()
        first, second = make_fill_set(session), make_fill_set(session, 2, SOL)
        assert await storage.write_fill_set(org, first)
        assert await storage.write_fill_set(org, second)
        assert await storage.read_fill_set(org, session, None) == second
        assert await storage.read_fill_set(org, session, 1) == first

    async def test_a_retry_writes_nothing(self, storage: FillSetStorageInterface) -> None:
        org, session = new_id(), new_id()
        first = make_fill_set(session)
        assert await storage.write_fill_set(org, first)
        changed = first.model_copy(update={"eligibility": Eligibility(zero_retention=True)})
        assert not await storage.write_fill_set(org, changed)
        assert await storage.read_fill_set(org, session, None) == first

    async def test_a_version_taken_by_another_writer_is_refused(
        self, storage: FillSetStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        first = make_fill_set(session)
        assert await storage.write_fill_set(org, first)
        with pytest.raises(PreconditionFailed):
            await storage.write_fill_set(org, make_fill_set(session, main=SOL))
        assert await storage.read_fill_set(org, session, None) == first

    async def test_write_fill_set_under_another_tenant_changes_nothing_here(
        self, storage: FillSetStorageInterface
    ) -> None:
        org, other, session = new_id(), new_id(), new_id()
        first = make_fill_set(session)
        assert await storage.write_fill_set(org, first)
        assert not await storage.write_fill_set(other, first)
        assert await storage.read_fill_set(other, session, None) is None
        assert await storage.read_fill_set(org, session, None) == first

    async def test_read_fill_set_under_another_tenant_finds_nothing(
        self, storage: FillSetStorageInterface
    ) -> None:
        org, other, session = new_id(), new_id(), new_id()
        assert await storage.write_fill_set(org, make_fill_set(session))
        assert await storage.read_fill_set(other, session, None) is None
        assert await storage.read_fill_set(other, session, 1) is None
