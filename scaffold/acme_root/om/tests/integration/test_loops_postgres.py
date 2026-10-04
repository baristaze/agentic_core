"""The loop over Postgres: model, tool, model, each step persisted before it
is acted on and its content sealed at rest; a lost run's open call
settled by a new run, whose epoch refuses the old one; and a child's
report in its parent's inbox, whole or by its handle."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from contracts.loops import (
    ASSISTANT,
    BUILDER,
    DELIVERY,
    LEAD,
    WORKER,
    call,
    loop_over,
    reply,
    said,
    use,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from acme.infra.impl.configured import InfraConfiguredImpl
from acme.infra.impl.settings import InfraSettings
from acme.om.agents.types.request import Spawn
from acme.om.agents.types.run import RunEnd
from acme.om.base import new_id
from acme.om.context import AppContext, AppType, RequestContext, TenantContext
from acme.om.root import build_managers
from acme.om.steps.types.content import TextBlock
from acme.om.steps.types.header import InputHeader, LoopOutcome, ParkReason
from acme.om.steps.types.step import StepType
from acme.om.storage.impl.postgres import StoragePostgresImpl
from acme.om.storage.settings import MigrationSettings
from acme.om.tools.types.call import JobCompletion

pytestmark = pytest.mark.integration

APP = AppContext(type=AppType.PORTAL, version="portal@test")


@pytest.fixture
async def storage(
    migration_settings: MigrationSettings, migrated: object
) -> AsyncIterator[StoragePostgresImpl]:
    root = StoragePostgresImpl(
        migration_settings.role_urls(),
        migration_settings.role_pools(),
        system_urls=migration_settings.system_role_urls(),
    )
    yield root
    await root.close()


async def an_owner(storage: StoragePostgresImpl, tmp_path: Path) -> TenantContext:
    settings = InfraSettings.model_validate(
        {"environment": "local", "buckets_root": tmp_path / "buckets"}
    )
    managers = build_managers(storage, InfraConfiguredImpl(settings))
    slug = f"ajax-{new_id().hex[-8:]}"
    owner, _ = await managers.tenancy.bootstrap(
        RequestContext(request_id=new_id(), app=APP),
        "Ajax",
        slug,
        f"ann-{slug}@example.test",
        "Ann",
    )
    return owner


async def test_a_loop_over_postgres_persists_each_step_first_and_seals_what_it_says(
    storage: StoragePostgresImpl, tmp_path: Path, migration_settings: MigrationSettings
) -> None:
    owner = await an_owner(storage, tmp_path)
    loop = loop_over(tmp_path, storage=storage, owner=owner)
    session_id = await loop.start()
    await loop.say(session_id, "What is the quarterly total?")
    loop.anthropic.add(reply(said("Looking."), use("lookup")), reply(said("The total is 12.")))

    run = await loop.loops.run(owner, session_id)

    assert (run.end, run.outcome) == (RunEnd.ENDED, LoopOutcome.SUCCEEDED)
    steps = await loop.history(session_id)
    assert [step.type for step in steps] == [
        StepType.MESSAGE,
        StepType.MODEL_REQUEST,
        StepType.MODEL_RESPONSE,
        StepType.TOOL_REQUEST,
        StepType.TOOL_RESPONSE,
        StepType.MODEL_REQUEST,
        StepType.MODEL_RESPONSE,
        StepType.LOOP_ENDED,
    ]
    assert [step.seq for step in steps] == list(range(1, 9))
    assert steps[-2].as_text() == "The total is 12."
    engine = create_async_engine(migration_settings.master_url())
    try:
        async with engine.connect() as connection:
            plain = await connection.scalar(
                text(
                    "SELECT count(*) FROM activity.steps WHERE session_id = :s "
                    "AND content::text LIKE '%total%'"
                ),
                {"s": session_id},
            )
    finally:
        await engine.dispose()
    assert plain == 0, "a database reader sees ciphertext"


async def test_a_new_run_over_postgres_refuses_the_lost_one_and_settles_its_call(
    storage: StoragePostgresImpl, tmp_path: Path
) -> None:
    owner = await an_owner(storage, tmp_path)
    loop = loop_over(tmp_path, storage=storage, owner=owner)
    session_id = await loop.start()
    await loop.say(session_id, "Note the fix.")
    loop.anthropic.add(
        reply(use("slow", use_id="use_slow"), use("note", use_id="use_note")),
        reply(said("The note did not finish; I checked.")),
    )
    slow, note = loop.tools["slow"], loop.tools["note"]
    lost = asyncio.ensure_future(loop.loops.run(owner, session_id))
    await slow.started.wait()

    run = await loop.loops.run(owner, session_id)
    slow.release.set()
    stale = await lost

    assert run.outcome is LoopOutcome.SUCCEEDED and stale.end is RunEnd.STALE
    assert note.ran_as == [] and len(slow.ran_as) == 2
    answers = [s for s in await loop.history(session_id) if s.type is StepType.TOOL_RESPONSE]
    assert len(answers) == 2


async def test_a_job_park_and_its_completion_round_trip_through_postgres(
    storage: StoragePostgresImpl, tmp_path: Path
) -> None:
    """The park keeps the job in the stored step and the session's row, and
    the completion read back answers the call."""
    owner = await an_owner(storage, tmp_path)
    loop = loop_over(tmp_path, storage=storage, owner=owner, kinds=(ASSISTANT, DELIVERY, BUILDER))
    session_id = await loop.start("builder")
    await loop.say(session_id, "Start it.")
    loop.anthropic.add(reply(call("build", q="everything")), reply(said("It is done.")))

    parked = await loop.loops.run(owner, session_id)

    assert parked.park is not None and parked.park.reason is ParkReason.JOB
    (request,) = [s for s in await loop.history(session_id) if s.type is StepType.TOOL_REQUEST]
    session = await loop.managers.agent_sessions.get_session(owner, session_id)
    assert session.park == parked.park
    job = parked.park.job
    assert job is not None and (job.key, job.handle) == (request.id, "build-1")
    completion = JobCompletion(key=request.id, handle="build-1", text="3 targets built")
    await loop.loops.complete_job(owner, session_id, completion)
    done = await loop.loops.run(owner, session_id)

    assert done.outcome is LoopOutcome.SUCCEEDED
    steps = await loop.history(session_id)
    answer = next(s for s in steps if s.responds_to == request.id)
    assert answer.as_tool_response().parts[0].text == "3 targets built"  # type: ignore[union-attr]


async def test_a_childs_report_wakes_its_parent_over_postgres_whole_or_by_its_handle(
    storage: StoragePostgresImpl, tmp_path: Path
) -> None:
    """A report within the bound lands whole; one above it lands as its
    head, its tail, and the handle of an artifact of the parent's session,
    which holds it whole and which the parent's next request names."""
    owner = await an_owner(storage, tmp_path)
    loop = loop_over(tmp_path, storage=storage, owner=owner, kinds=(LEAD, WORKER))
    parent_id = await loop.start(LEAD.name)
    await loop.say(parent_id, "What are the quarterly totals?")
    loop.anthropic.add(reply(said("Two sub-agents will find them.")))
    await loop.loops.run(owner, parent_id)
    middle = "the middle of the ledger"
    long = "A" * 15_000 + middle + "B" * 15_000
    reports = []
    for answer in ("The total is 12.", long):
        before = len(await loop.history(parent_id))
        child = await loop.managers.agents.spawn(
            owner, parent_id, Spawn(id=new_id(), kind="worker", title="a total", objective="Sum.")
        )
        (objective,) = await loop.history(child.id)
        evidence = [str(objective.id)]
        loop.anthropic.add(
            reply(said(answer), call("submit", claim="succeeded", evidence=evidence))
        )

        run = await loop.loops.run(owner, child.id)

        assert run.outcome is LoopOutcome.SUCCEEDED
        (report,) = (await loop.history(parent_id))[before:]
        header = report.header
        assert isinstance(header, InputHeader) and header.waking is True
        assert header.agent is not None and header.agent.session_id == child.id
        reports.append(report)

    whole, kept = reports
    assert isinstance(whole.header, InputHeader) and whole.header.artifact is None
    assert "ended succeeded" in whole.as_text() and "The total is 12." in whole.as_text()
    assert isinstance(kept.header, InputHeader) and kept.header.artifact is not None
    handle = kept.header.artifact
    head, tail = kept.content.blocks
    assert isinstance(head, TextBlock) and isinstance(tail, TextBlock)
    assert head.text.startswith("Sub-agent") and tail.text.endswith("B" * 100)
    assert middle not in kept.as_text(), "the parent's step holds the preview alone"
    read, offset, more = "", 0, True
    while more:
        page = await loop.managers.windows.get_artifact(owner, parent_id, handle.id, offset, 24_000)
        read, offset, more = read + page.text, offset + len(page.text), page.has_more
    assert len(read) == handle.characters and middle in read and long in read

    loop.anthropic.add(reply(said("The totals are in.")))
    await loop.loops.run(owner, parent_id)

    sent = "\n".join(
        block.text
        for message in loop.anthropic.calls[-1].messages
        for block in message.blocks
        if isinstance(block, TextBlock)
    )
    assert f"The whole report is artifact {handle.id}" in sent and middle not in sent
