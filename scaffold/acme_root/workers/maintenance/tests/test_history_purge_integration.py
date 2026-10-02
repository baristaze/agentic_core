"""The sweep purges a deleted tenant's history over Postgres, as the worker
runs it: its storage built from its settings, the purge login's pool beside
the runtime and system logins', and every delete of a step under the purge
login, the one login the database lets delete one."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from worker_support import request

from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.base import new_id, utcnow
from acme.om.steps.types.header import InputHeader
from acme.om.steps.types.page import StepCursor
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.main import build_loop
from acme.workers.maintenance.settings import MaintenanceSettings

pytestmark = pytest.mark.integration


@pytest.fixture
async def container(tmp_path: Path) -> AsyncIterator[WorkerContainer]:
    """The worker over the database its settings name, refused unless it is
    local, with everything but storage in the process."""
    settings = MaintenanceSettings(
        cache_backend="memory",
        topics_backend="memory",
        buckets_backend="local",
        buckets_root=tmp_path / "buckets",
        queues_backend="memory",
        secrets_backend="local",
        sentry_dsn=None,
        otel_endpoint=None,
        worker_id="purge-integration",
    )
    settings.refuse_remote()
    built = WorkerContainer.build(settings)
    yield built
    await built.close()


def a_session() -> AgentSession:
    now, by, session_id = utcnow(), new_id(), new_id()
    return AgentSession(
        id=session_id,
        created_at=now,
        updated_at=now,
        created_by=by,
        updated_by=by,
        title="the weekly report",
        root_id=session_id,
    )


def a_message(session_id: UUID) -> Step:
    step_id = new_id()
    return Step(
        id=step_id,
        created_at=utcnow(),
        session_id=session_id,
        loop_id=step_id,
        type=StepType.MESSAGE,
        actor=Actor.PERSON,
        origin=Origin.PORTAL,
        header=InputHeader(),
    )


async def test_a_deleted_tenants_sessions_steps_and_cursors_go_before_it_is_marked_purged(
    container: WorkerContainer,
) -> None:
    """A tenant deleted past its retention. A first pass takes its people
    and its credentials. Then two sessions and their histories are all it
    keeps: the next pass deletes every session, step, and cursor row, and
    leaves the tenant unmarked, since they were there to delete; the pass
    after finds nothing and marks it purged."""
    tail = new_id().hex[-8:]
    _, org = await container.managers.tenancy.bootstrap(
        request(), "Gone", f"gone-{tail}", f"gone-{tail}@example.test", "Gone"
    )
    tenancy = container.storage.get_tenancy_storage()
    stored = await tenancy.read_org(org.id)
    assert stored is not None
    await tenancy.write_org(
        org.id, stored.model_copy(update={"deleted_at": utcnow() - timedelta(days=40)})
    )
    loop = build_loop(container)
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]

    sessions = container.storage.get_agent_session_storage()
    history = container.storage.get_step_storage()
    made = [a_session(), a_session()]
    for session in made:
        assert await sessions.create_session(org.id, session, ())
        await history.append_inputs(org.id, session.id, [a_message(session.id) for _ in range(3)])
        await history.begin_run(org.id, session.id)
    before = await tenancy.read_org(org.id)
    assert before is not None and before.purged_at is None

    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert await sessions.read_sessions(org.id, None, None, 10) == []
    for session in made:
        assert await sessions.read_session(org.id, session.id) is None
        assert await history.read_steps(org.id, session.id, 0, 10) == []
        assert await history.read_cursor(org.id, session.id) == StepCursor()
    taken = await tenancy.read_org(org.id)
    assert taken is not None and taken.purged_at is None, "its history was there to delete"

    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    marked = await tenancy.read_org(org.id)
    assert marked is not None and marked.purged_at is not None, "nothing was left"
