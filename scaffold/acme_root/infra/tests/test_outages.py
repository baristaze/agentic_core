"""The outage signal contract: what every impl of `OutageSignalInterface` that
signals holds, over the memory cache and over Valkey through the configured
root, behind its breaker. A report is known until its retry time and not at
it; a later report extends it and an earlier one never shortens it; a
success clears it; and the pair is the key, so another provider or another
credential knows nothing of it. Every case passes its own `now`, so no case
reads the wall clock. Then the null, which never signals."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from acme.infra.base import SYSTEM_SCOPE, QuietNull, new_id
from acme.infra.cache import CacheInterface, CacheScope
from acme.infra.impl.configured import InfraConfiguredImpl
from acme.infra.impl.local import InfraLocalImpl
from acme.infra.impl.settings import InfraSettings
from acme.infra.outages import Outage, OutageSignalInterface
from acme.infra.outages.null import OutageSignalNullImpl
from acme.infra.outages.shared import outage_key

AT = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def outage(provider: str, credential: str, *, seconds: int, kind: str = "overloaded") -> Outage:
    return Outage(
        provider=provider,
        credential=credential,
        kind=kind,
        retry_at=AT + timedelta(seconds=seconds),
    )


def pair() -> tuple[str, str]:
    """A provider and a credential no other case names, so a store that
    outlives a case (Valkey) never answers one case with another's."""
    return f"provider-{new_id().hex}", f"credential-{new_id().hex}"


class OutageSignalContract:
    @pytest.fixture
    def signal(self) -> OutageSignalInterface:
        raise NotImplementedError("the concrete test class provides the signal")

    @pytest.fixture
    def cache(self) -> CacheInterface:
        raise NotImplementedError("the concrete test class provides the cache under it")

    async def test_a_report_is_known_until_its_retry_time_and_not_at_it(
        self, signal: OutageSignalInterface
    ) -> None:
        provider, credential = pair()
        reported = outage(provider, credential, seconds=60)
        await signal.report(reported, AT)
        assert await signal.current(provider, credential, AT) == reported
        assert await signal.current(provider, credential, AT + timedelta(seconds=59)) == reported
        assert await signal.current(provider, credential, AT + timedelta(seconds=60)) is None
        assert await signal.current(provider, credential, AT + timedelta(seconds=61)) is None

    async def test_a_later_report_extends_and_an_earlier_one_never_shortens(
        self, signal: OutageSignalInterface
    ) -> None:
        provider, credential = pair()
        await signal.report(outage(provider, credential, seconds=30), AT)
        longer = outage(provider, credential, seconds=90, kind="rate_limited")
        await signal.report(longer, AT + timedelta(seconds=1))
        assert await signal.current(provider, credential, AT + timedelta(seconds=2)) == longer
        await signal.report(outage(provider, credential, seconds=45), AT + timedelta(seconds=3))
        assert await signal.current(provider, credential, AT + timedelta(seconds=60)) == longer

    async def test_a_report_already_past_records_nothing(
        self, signal: OutageSignalInterface
    ) -> None:
        provider, credential = pair()
        await signal.report(outage(provider, credential, seconds=0), AT)
        await signal.report(outage(provider, credential, seconds=-5), AT)
        assert await signal.current(provider, credential, AT - timedelta(seconds=10)) is None

    async def test_a_success_clears_what_was_known(self, signal: OutageSignalInterface) -> None:
        provider, credential = pair()
        await signal.report(outage(provider, credential, seconds=60), AT)
        await signal.clear(provider, credential)
        assert await signal.current(provider, credential, AT) is None
        await signal.clear(provider, credential)  # clearing nothing is nothing

    async def test_the_provider_and_the_credential_together_are_the_key(
        self, signal: OutageSignalInterface
    ) -> None:
        provider, credential = pair()
        other_provider, other_credential = pair()
        await signal.report(outage(provider, credential, seconds=60), AT)
        assert await signal.current(provider, other_credential, AT) is None
        assert await signal.current(other_provider, credential, AT) is None
        # A name that holds the separator reaches no other pair's entry.
        await signal.report(outage("a:b", "c", seconds=60), AT)
        assert await signal.current("a", "b:c", AT) is None
        await signal.clear(provider, other_credential)
        assert await signal.current(provider, credential, AT) is not None
        await signal.clear("a:b", "c")

    async def test_a_value_no_report_wrote_says_nothing_is_known(
        self, signal: OutageSignalInterface, cache: CacheInterface
    ) -> None:
        provider, credential = pair()
        key = outage_key(provider, credential)
        await cache.put(SYSTEM_SCOPE, key, b"not an outage", timedelta(seconds=60))
        assert await signal.current(provider, credential, AT) is None
        await cache.invalidate(SYSTEM_SCOPE, key)


class TestOutageSignalMemory(OutageSignalContract):
    @pytest.fixture
    def infra(self, tmp_path: Path) -> InfraLocalImpl:
        return InfraLocalImpl(tmp_path)

    @pytest.fixture
    def signal(self, infra: InfraLocalImpl) -> OutageSignalInterface:
        return infra.get_outages()

    @pytest.fixture
    def cache(self, infra: InfraLocalImpl) -> CacheInterface:
        return infra.get_cache(CacheScope.OUTAGE)


@pytest.mark.integration
class TestOutageSignalValkey(OutageSignalContract):
    @pytest.fixture
    async def infra(self) -> AsyncIterator[InfraConfiguredImpl]:
        settings = InfraSettings.model_validate(
            {
                "environment": "local",
                "cache_backend": "valkey",
                "valkey_url": InfraSettings().valkey_url,
            }
        )
        root = InfraConfiguredImpl(settings)
        await root.start()
        try:
            yield root
        finally:
            await root.close()

    @pytest.fixture
    def signal(self, infra: InfraConfiguredImpl) -> OutageSignalInterface:
        assert "valkey" in infra.get_outages().describe()
        return infra.get_outages()

    @pytest.fixture
    def cache(self, infra: InfraConfiguredImpl) -> CacheInterface:
        return infra.get_cache(CacheScope.OUTAGE)


async def test_the_null_signal_is_quiet_and_never_signals() -> None:
    signal = OutageSignalNullImpl()
    assert isinstance(signal, QuietNull)
    await signal.report(outage("anthropic", "platform", seconds=60), AT)
    assert await signal.current("anthropic", "platform", AT) is None
    await signal.clear("anthropic", "platform")
    assert signal.describe() == "outages=none"


def test_a_root_shares_its_signal_on_the_cache_it_built(tmp_path: Path) -> None:
    """The signal follows the cache backend, so a deployed process, which
    refuses a memory cache, shares its signal on Valkey."""
    memory = InfraLocalImpl(tmp_path)
    assert memory.get_outages() is memory.get_outages()
    assert memory.get_outages().describe() == "outages=shared(cache[outage]=memory)"
