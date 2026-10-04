"""The operator's read of one session's usage, through the API over Postgres:
a read operator's token reads the session's records a page at a time, with
the rollup of each loop and of the whole session; a tenant's own token is
no operator's; and another tenant's session, named under this tenant,
reads as one that never called (ADR 1014)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from api_support import OWNER, TOTP_KEY, client_over, enrol_operator, seed_request, sign_in_as

from acme.om.base import new_id, utcnow
from acme.om.budgets.types.usage import UsageRecord
from acme.om.context import OperatorRole
from acme.services.api.container import AppContainer
from acme.services.api.settings import ApiSettings

pytestmark = pytest.mark.integration


def settings_over_postgres(tmp_path: Path) -> ApiSettings:
    """The database from ACME_DATABASE_URL and everything else in the
    process; refused unless the database is local."""
    settings = ApiSettings(
        **{
            "cache_backend": "memory",
            "topics_backend": "memory",
            "buckets_backend": "local",
            "buckets_root": tmp_path / "buckets",
            "queues_backend": "memory",
            "secrets_backend": "local",
            "dev_sign_in_enabled": True,
            "totp_encryption_key": TOTP_KEY,
            "sentry_dsn": None,
            "otel_endpoint": None,
        }
    )
    settings.refuse_remote()
    return settings


@asynccontextmanager
async def over_postgres(
    tmp_path: Path,
) -> AsyncIterator[tuple[AppContainer, httpx.AsyncClient, dict[str, str], UUID]]:
    """The API over Postgres, the headers of a fresh owner's session, and its
    org."""
    container = AppContainer.build(settings_over_postgres(tmp_path))
    async with client_over(container) as client:
        suffix = new_id().hex[-8:]
        email = f"ann-{suffix}@example.test"
        _, org = await container.managers.tenancy.bootstrap(
            seed_request(), "Ajax", f"ajax-{suffix}", email, OWNER["name"]
        )
        yield container, client, await sign_in_as(client, email, org.id), org.id


def a_record(session_id: UUID, loop_id: UUID, input_tokens: int, cost: int | None) -> UsageRecord:
    return UsageRecord(
        id=new_id(),
        created_at=utcnow(),
        hold_id=new_id(),
        session_id=session_id,
        tree_id=session_id,
        loop_id=loop_id,
        step_id=new_id(),
        agent_kind="assistant",
        kind_version=1,
        role="main",
        provider="anthropic",
        model="claude-sonnet",
        input_tokens=input_tokens,
        cache_read_tokens=500,
        cache_write_tokens=100,
        output_tokens=40,
        thinking_tokens=0,
        cost_micros=cost,
        latency_ms=800,
    )


async def test_a_read_operator_reads_a_sessions_usage_and_nobody_else_does(
    tmp_path: Path,
) -> None:
    async with over_postgres(tmp_path) as (container, client, owner, org_id):
        ledger = container.storage.get_ledger_storage()
        session, first, second = new_id(), new_id(), new_id()
        records = [
            a_record(session, first, 1_000, 2_000),
            a_record(session, second, 2_000, 3_000),
            a_record(session, first, 3_000, None),
        ]
        for record in records:
            assert await ledger.append_usage_record(org_id, record)
        other_org, other_session = new_id(), new_id()
        assert await ledger.append_usage_record(
            other_org, a_record(other_session, new_id(), 9_000, 9_000)
        )
        reader, _ = await enrol_operator(
            client, container, f"sup-{new_id().hex[-8:]}@example.test", OperatorRole.READ
        )
        path = f"/v1/admin/orgs/{org_id}/sessions/{session}/usage"

        page = await client.get(path, headers=reader, params={"limit": 2})
        assert page.status_code == 200, page.text
        body: dict[str, Any] = page.json()
        assert [i["id"] for i in body["items"]] == [str(r.id) for r in records[:2]]
        assert body["next_cursor"] is not None
        rest = await client.get(
            path, headers=reader, params={"limit": 2, "cursor": body["next_cursor"]}
        )
        assert rest.status_code == 200, rest.text
        assert [i["id"] for i in rest.json()["items"]] == [str(records[2].id)]
        assert rest.json()["next_cursor"] is None
        assert {k: rest.json()[k] for k in ("loops", "total")} == {
            k: body[k] for k in ("loops", "total")
        }, "the rollups cover every record, whatever the page"
        assert [(u["loop_id"], u["rollup"]["calls"]) for u in body["loops"]] == [
            (str(first), 2),
            (str(second), 1),
        ]
        assert body["total"] == {
            "calls": 3,
            "input_tokens": 6_000,
            "cache_read_tokens": 1_500,
            "cache_write_tokens": 300,
            "output_tokens": 120,
            "thinking_tokens": 0,
            "cost_micros": 5_000,
            "unpriced": 1,
            "settled_whole": 0,
            "latency_ms": 2_400,
        }
        assert body["has_more_loops"] is False
        item = body["items"][0]
        assert (item["input_tokens"], item["cost_micros"], item["latency_ms"]) == (
            1_000,
            2_000,
            800,
        )

        as_tenant = await client.get(path, headers=owner)
        assert as_tenant.status_code == 401, as_tenant.text
        crossed = await client.get(
            f"/v1/admin/orgs/{org_id}/sessions/{other_session}/usage", headers=reader
        )
        assert crossed.status_code == 404, crossed.text
        assert crossed.json()["error"]["code"] == "not_found"
        assert "9000" not in crossed.text and str(other_org) not in crossed.text
