"""The sweep purges a deleted tenant's history over Postgres, as the worker
runs it: its storage built from its settings, the purge login's pool beside
the runtime and system logins', and every delete of a step under the purge
login, the one login the database lets delete one. Two workers that purge
one tenant take turns."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import delete, select
from worker_support import request

from acme.om.agent_sessions.storage.tables.agent_sessions import AgentSessions
from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.base import new_id, utcnow
from acme.om.steps.types.header import InputHeader
from acme.om.steps.types.page import StepCursor
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.storage.impl.pg_base import hold_purge, set_scope
from acme.om.storage.impl.postgres import login_sessions
from acme.om.storage.roles import DatabaseRole
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.main import build_loop
from acme.workers.maintenance.settings import MaintenanceSettings

pytestmark = pytest.mark.integration


def worker(tmp_path: Path, name: str, **overrides: Any) -> WorkerContainer:
    """A worker over the database its settings name, refused unless it is
    local, with everything but storage in the process."""
    settings = MaintenanceSettings(
        cache_backend="memory",
        topics_backend="memory",
        buckets_backend="local",
        buckets_root=tmp_path / name / "buckets",
        queues_backend="memory",
        secrets_backend="local",
        sentry_dsn=None,
        otel_endpoint=None,
        worker_id=name,
        **overrides,
    )
    settings.refuse_remote()
    return WorkerContainer.build(settings)


@pytest.fixture
async def container(tmp_path: Path) -> AsyncIterator[WorkerContainer]:
    built = worker(tmp_path, "purge-integration")
    yield built
    await built.close()


async def deleted_org(container: WorkerContainer) -> UUID:
    """A team org deleted 40 days ago, past its retention."""
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
    return org.id


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
    org_id = await deleted_org(container)
    tenancy = container.storage.get_tenancy_storage()
    loop = build_loop(container)
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]

    sessions = container.storage.get_agent_session_storage()
    history = container.storage.get_step_storage()
    made = await with_history(container, org_id, sessions=2, steps=3)
    before = await tenancy.read_org(org_id)
    assert before is not None and before.purged_at is None

    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert await sessions.read_sessions(org_id, None, None, 10) == []
    for session in made:
        assert await sessions.read_session(org_id, session.id) is None
        assert await history.read_steps(org_id, session.id, 0, 10) == []
        assert await history.read_cursor(org_id, session.id) == StepCursor()
    taken = await tenancy.read_org(org_id)
    assert taken is not None and taken.purged_at is None, "its history was there to delete"

    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    marked = await tenancy.read_org(org_id)
    assert marked is not None and marked.purged_at is not None, "nothing was left"


async def with_history(
    container: WorkerContainer, org_id: UUID, *, sessions: int, steps: int
) -> list[AgentSession]:
    """Sessions of the tenant, each with a history of `steps` and a cursor."""
    rows = container.storage.get_agent_session_storage()
    history = container.storage.get_step_storage()
    made = [a_session() for _ in range(sessions)]
    for session in made:
        assert await rows.create_session(org_id, session, ())
        if steps:
            await history.append_inputs(
                org_id, session.id, [a_message(session.id) for _ in range(steps)]
            )
            await history.begin_run(org_id, session.id)
    return made


async def left_of(container: WorkerContainer, org_id: UUID, made: list[AgentSession]) -> int:
    """The sessions, steps, and cursor rows of the tenant still there."""
    rows = container.storage.get_agent_session_storage()
    history = container.storage.get_step_storage()
    left = 0
    for session in made:
        left += await rows.read_session(org_id, session.id) is not None
        left += len(await history.read_steps(org_id, session.id, 0, 100))
        left += await history.read_cursor(org_id, session.id) != StepCursor()
    return left


async def test_a_sweep_that_meets_another_workers_purge_waits_and_marks_only_once_none_is_left(
    container: WorkerContainer, tmp_path: Path
) -> None:
    """Two workers sweep one deleted tenant with three sessions left, each
    purge taking two. The first worker is inside its purge: it holds the
    tenant's purge lock, as every purge does, and has deleted two sessions
    without committing. The second worker's pass waits for it, then deletes
    the session left, so it leaves the tenant unmarked; the pass after marks
    it. Counting nothing while the first batch was in flight would have
    marked the tenant with a session left."""
    org_id = await deleted_org(container)
    second = worker(tmp_path, "purge-second", worker_purge_batch=2)
    settings = second.settings
    first, engines = login_sessions(
        settings.role_urls(),
        settings.role_pools(),
        system_urls=settings.system_role_urls(),
        purge_urls=settings.purge_role_urls(),
    )
    tenancy = second.storage.get_tenancy_storage()
    try:
        await build_loop(second)._sweep_once()  # pyright: ignore[reportPrivateUsage]
        made = await with_history(second, org_id, sessions=3, steps=0)
        assert first.purge is not None
        async with first.purge[DatabaseRole.CORE]() as in_flight:
            await set_scope(in_flight, org_id, None, None)
            await hold_purge(in_flight, DatabaseRole.CORE, org_id)
            batch = select(AgentSessions.id).where(AgentSessions.org_id == org_id).limit(2)
            await in_flight.execute(
                delete(AgentSessions).where(
                    AgentSessions.org_id == org_id, AgentSessions.id.in_(batch)
                )
            )
            sweep = asyncio.create_task(build_loop(second)._sweep_once())  # pyright: ignore[reportPrivateUsage]
            await asyncio.sleep(0.5)
            assert not sweep.done(), "the pass did not meet the purge in flight"
            await in_flight.commit()
        await sweep
        assert await left_of(second, org_id, made) == 0
        met = await tenancy.read_org(org_id)
        assert met is not None and met.purged_at is None, "the last session was its to delete"
        await build_loop(second)._sweep_once()  # pyright: ignore[reportPrivateUsage]
        marked = await tenancy.read_org(org_id)
        assert marked is not None and marked.purged_at is not None, "nothing was left"
    finally:
        await second.close()
        for engine in engines.values():
            await engine.dispose()
