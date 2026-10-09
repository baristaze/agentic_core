"""The outage signal a fleet shares, on Valkey: one session learns that a
provider is failing, and a second parks at once, before it calls, naming the
provider, and wakes at the retry time."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from contracts.loops import loop_over, outage_parks_at_once_and_resumes_at_the_retry_time

from acme.infra.base import SYSTEM_SCOPE, new_id
from acme.infra.impl.configured import InfraConfiguredImpl
from acme.infra.impl.settings import InfraSettings
from acme.om.agents.impl.loop import LoopOptions

pytestmark = pytest.mark.integration


@pytest.fixture
async def shared() -> AsyncIterator[InfraConfiguredImpl]:
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


async def test_a_known_outage_on_the_shared_signal_parks_at_once_and_resumes_at_the_retry_time(
    tmp_path: Path, shared: InfraConfiguredImpl
) -> None:
    signal = shared.get_outages()
    assert "valkey" in signal.describe()
    # A credential of this case's own, so no other process reading the same
    # Valkey learns of an outage that never was.
    credential = f"case-{new_id().hex}"
    options = LoopOptions(credential=credential, control_poll=timedelta(milliseconds=1))
    try:
        await outage_parks_at_once_and_resumes_at_the_retry_time(
            loop_over(tmp_path, outages=signal, options=options)
        )
    finally:
        await signal.clear(SYSTEM_SCOPE, "anthropic", credential)
