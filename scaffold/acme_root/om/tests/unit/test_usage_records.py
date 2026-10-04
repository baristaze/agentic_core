"""A model call's usage record: written once per billed call in every storage
mode, holding no content, and kept by a session's purge and a tenant's
(ADR 1014)."""

from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.loops import ASSISTANT, Loop, loop_over, reply, said, use
from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Text, Uuid

from acme.integrations.model_providers.calls import ModelCall
from acme.integrations.model_providers.scripted import ScriptedFailure
from acme.integrations.model_providers.types import ErrorKind, ProviderName, Usage
from acme.om.agent_sessions.impl.manager import AgentSessionsOptions
from acme.om.agents.types.request import Start
from acme.om.base import new_id
from acme.om.budgets.rules import usage_spend
from acme.om.budgets.storage.tables.usage_records import UsageRecords
from acme.om.budgets.types.hold import Settlement
from acme.om.budgets.types.usage import UsageRecord
from acme.om.context import TenantContext
from acme.om.exceptions import Unavailable
from acme.om.models.types.fill import MAIN, SUMMARIZER
from acme.om.privacy.types.session_privacy import StorageMode, StoragePolicy
from acme.om.steps.types.header import LoopOutcome, ModelRequestHeader
from acme.om.steps.types.step import Step, StepType

FIRST = Usage(input=1_200, cache_read=800, cache_write=300, output=45, thinking=20)
SECOND = Usage(input=1_400, cache_read=1_100, cache_write=0, output=60, thinking=0)
LATENCY = timedelta(milliseconds=1_250)

MODES: dict[str, StoragePolicy | None] = {
    "sealed": None,
    "memory_only with keep_shape": StoragePolicy(mode=StorageMode.MEMORY_ONLY, keep_shape=True),
    "memory_only without keep_shape": StoragePolicy(mode=StorageMode.MEMORY_ONLY, keep_shape=False),
}


def timed(loop: Loop, monkeypatch: pytest.MonkeyPatch) -> None:
    """The provider takes `LATENCY` to answer each call, on the loop's clock."""
    stream = loop.anthropic.stream

    async def slow(call: ModelCall):
        loop.clock.now += LATENCY
        async for part in stream(call):
            yield part

    monkeypatch.setattr(loop.anthropic, "stream", slow)


async def started(loop: Loop, title: str = "a question") -> UUID:
    session = await loop.managers.agents.start_session(
        loop.owner, Start(id=new_id(), kind=ASSISTANT.name, title=title)
    )
    return session.id


async def one_loop(loop: Loop, session: UUID, *, question: str, q: str, answer: str) -> None:
    """A message, then one loop of two model calls: a tool use, and the
    answer the tool's result led to."""
    await loop.say(session, question)
    loop.anthropic.add(
        reply(use("lookup", q)).model_copy(update={"usage": FIRST}),
        reply(said(answer)).model_copy(update={"usage": SECOND}),
    )
    await loop.loops.run(loop.owner, session)


async def records_of(loop: Loop, ctx: TenantContext, session: UUID) -> list[UsageRecord]:
    return await loop.storage.get_ledger_storage().read_usage_records(
        ctx.org_id, session, None, 100
    )


def figures(record: UsageRecord) -> tuple[object, ...]:
    """What a record says of its call, apart from its ids and its time."""
    return (
        record.agent_kind,
        record.role,
        record.provider,
        record.model,
        record.input_tokens,
        record.cache_read_tokens,
        record.cache_write_tokens,
        record.output_tokens,
        record.thinking_tokens,
        record.cost_micros,
        record.latency_ms,
    )


async def test_every_storage_mode_keeps_one_usage_record_per_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One loop under each mode, the same two calls: each mode reads back one
    record per call, with every class of token, the cost at the model's list
    price, and the provider's latency, and the three agree. The mode that
    keeps no shape keeps no step at rest, and its cost survives all the
    same."""
    loop = loop_over(tmp_path)
    timed(loop, monkeypatch)
    org = loop.owner.org_id
    found: dict[str, list[tuple[object, ...]]] = {}
    sessions: list[UUID] = []
    for mode, policy in MODES.items():
        session = await started(loop)
        sessions.append(session)
        if policy is not None:
            await loop.managers.privacy.set_policy(loop.owner, session, policy)
        await one_loop(loop, session, question="What is the total?", q="the total", answer="12.")
        records = await records_of(loop, loop.owner, session)
        responses = [s for s in await loop.history(session) if s.type is StepType.MODEL_RESPONSE]
        assert [r.step_id for r in records] == [s.id for s in responses], mode
        assert len({r.loop_id for r in records}) == 1 and records[0].loop_id == responses[0].loop_id
        assert len({r.hold_id for r in records}) == 2, f"{mode}: one record per hold"
        row = await loop.managers.agent_sessions.get_session(loop.owner, session)
        assert {(r.tree_id, r.kind_version) for r in records} == {(row.root_id, row.kind_version)}
        at_rest = await loop.storage.get_step_storage().read_steps(org, session, 0, 100)
        assert bool(at_rest) is (mode != "memory_only without keep_shape"), mode
        found[mode] = [figures(r) for r in records]

    fill = (await loop.managers.models.get_fill_set(loop.owner, sessions[0])).fill_for(MAIN)
    assert fill is not None
    price = loop.managers.pricing.price_of(fill.provider.value, fill.model)
    assert price is not None, "the assistant's model has a price"
    expected = [
        (
            ASSISTANT.name,
            MAIN,
            fill.provider.value,
            fill.model,
            usage.input,
            usage.cache_read,
            usage.cache_write,
            usage.output,
            usage.thinking,
            usage_spend(usage, price).cost_micros,
            1_250,
        )
        for usage in (FIRST, SECOND)
    ]
    assert all(cost is not None for *_, cost, _ in expected)
    assert found == dict.fromkeys(MODES, expected), "every mode keeps the same cost"


LABELS = {"agent_kind", "role", "provider", "model"}
COLUMNS = {
    "id": Uuid,
    "org_id": Uuid,
    "created_at": DateTime,
    "hold_id": Uuid,
    "session_id": Uuid,
    "tree_id": Uuid,
    "loop_id": Uuid,
    "step_id": Uuid,
    "agent_kind": Text,
    "kind_version": Integer,
    "role": Text,
    "provider": Text,
    "model": Text,
    "input_tokens": BigInteger,
    "cache_read_tokens": BigInteger,
    "cache_write_tokens": BigInteger,
    "output_tokens": BigInteger,
    "thinking_tokens": BigInteger,
    "cost_micros": BigInteger,
    "latency_ms": BigInteger,
    "settled_whole": Boolean,
}
"""Every column a usage record has: ids, a time, counts, money, a duration,
four labels, and a mark. A column added here is a decision that it holds
no content."""


async def test_a_usage_record_holds_no_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record's columns are ids, counts, money, a duration, and labels,
    and nothing else; each label is a name the product or its catalog
    defines. A loop whose title, message, tool input, tool output, and reply
    each carry a marker leaves records that carry none of them, in a mode
    that keeps no content at rest."""
    assert {c.name: type(c.type) for c in UsageRecords.__table__.columns} == COLUMNS
    assert set(UsageRecord.model_fields) == set(COLUMNS) - {"org_id"}
    loop = loop_over(tmp_path)
    timed(loop, monkeypatch)
    markers = {
        "title": "zebra-title-7781",
        "message": "zebra-message-7782",
        "tool input": "zebra-input-7783",
        "reply": "zebra-reply-7784",
    }
    session = await started(loop, title=markers["title"])
    policy = StoragePolicy(mode=StorageMode.MEMORY_ONLY, keep_shape=False)
    await loop.managers.privacy.set_policy(loop.owner, session, policy)
    await one_loop(
        loop,
        session,
        question=f"Find {markers['message']}",
        q=markers["tool input"],
        answer=markers["reply"],
    )
    history = await loop.history(session)
    output = next(s for s in history if s.type is StepType.TOOL_RESPONSE).as_tool_response()
    assert markers["tool input"] in str(output.parts), "the tool's output carries its input"
    records = await records_of(loop, loop.owner, session)
    assert len(records) == 2
    for record in records:
        kept = record.model_dump_json()
        assert not [what for what, marker in markers.items() if marker in kept], kept
        assert record.agent_kind == ASSISTANT.name
        assert record.role in {MAIN, SUMMARIZER}
        assert record.provider in {p.value for p in ProviderName}
        assert loop.managers.pricing.price_of(record.provider, record.model) is not None


async def test_a_sessions_purge_keeps_its_usage_records(tmp_path: Path) -> None:
    """The purge that takes a session's history and its row (ADR 1010)
    leaves its usage records: billing data with no content, kept as the
    ledger is."""
    loop = loop_over(tmp_path, sessions=AgentSessionsOptions(retention=timedelta(0)))
    session = await started(loop)
    await one_loop(loop, session, question="What is the total?", q="the total", answer="12.")
    kept = await records_of(loop, loop.owner, session)
    assert len(kept) == 2
    await loop.managers.agent_sessions.delete_session(loop.owner, session)
    assert await loop.managers.agent_sessions.purge_across_tenants() == 1
    org = loop.owner.org_id
    assert await loop.storage.get_step_storage().read_steps(org, session, 0, 10) == []
    assert await loop.storage.get_agent_session_storage().read_session(org, session) is None
    assert await records_of(loop, loop.owner, session) == kept, "the records stay whole"


async def test_a_deleted_tenants_purge_keeps_its_usage_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tenant past its retention loses its history, its sessions, their
    keys, and its budgets, and keeps its usage records, as it keeps its
    ledger. The ledger's step of the sweep never counts them, so they never
    keep the tenant from being marked purged (the sweep's own test)."""
    loop = loop_over(tmp_path)
    session = await started(loop)
    await one_loop(loop, session, question="What is the total?", q="the total", answer="12.")
    kept = await records_of(loop, loop.owner, session)
    assert len(kept) == 2
    org = loop.owner.org_id

    async def expired(asked: TenantContext) -> bool:
        return asked.org_id == org

    managers = loop.managers
    monkeypatch.setattr(managers.tenancy, "tenant_expired", expired)
    for purge in (
        managers.steps.purge_tenant,
        managers.agent_sessions.purge_tenant,
        managers.privacy.purge_tenant,
        managers.budgets.purge_tenant,
    ):
        while await purge(loop.owner):
            pass
    assert await loop.storage.get_step_storage().read_steps(org, session, 0, 10) == []
    assert await loop.storage.get_agent_session_storage().read_session(org, session) is None
    assert await records_of(loop, loop.owner, session) == kept, "the records stay whole"


PARTIAL = Usage(input=1_200, cache_read=800, output=3)


def is_request(step: Step) -> bool:
    return isinstance(step.header, ModelRequestHeader)


async def settlement_of(loop: Loop, request: Step) -> Settlement:
    """How the ledger closed a model request's hold."""
    header = request.header
    assert isinstance(header, ModelRequestHeader) and header.hold_id is not None
    found = await loop.storage.get_ledger_storage().read_settlement(
        loop.owner.org_id, header.hold_id
    )
    assert found is not None
    return found


async def test_a_call_settled_whole_leaves_a_marked_record_at_its_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stream that broke after it began is settled at its whole hold, then
    the call again is answered whole: two records, the first marked and
    holding what the partial reply reported, and a rollup whose cost is the
    ledger's settled total, the call settled whole counted apart."""
    loop = loop_over(tmp_path)
    timed(loop, monkeypatch)
    org = loop.owner.org_id
    session = await started(loop)
    await loop.say(session, "What is the total?")
    partial = reply(said("The total is")).model_copy(
        update={"stop_reason": None, "truncated": True, "usage": PARTIAL}
    )
    loop.anthropic.add(
        ScriptedFailure(kind=ErrorKind.TRANSIENT, partial=partial),
        reply(said("It is 12.")).model_copy(update={"usage": FIRST}),
    )

    run = await loop.loops.run(loop.owner, session)

    assert run.outcome is LoopOutcome.SUCCEEDED
    history = await loop.history(session)
    ledger = loop.storage.get_ledger_storage()
    first, second = [await settlement_of(loop, s) for s in history if is_request(s)]
    assert (first.bill.kind, second.bill.kind) == ("unknown", "billed")
    broken, whole = await records_of(loop, loop.owner, session)
    responses = [s.id for s in history if s.type is StepType.MODEL_RESPONSE]
    assert [broken.step_id, whole.step_id] == responses
    assert (broken.settled_whole, whole.settled_whole) == (True, False)
    assert (broken.input_tokens, broken.cache_read_tokens, broken.output_tokens) == (1_200, 800, 3)
    assert (broken.cost_micros, whole.cost_micros) == (
        first.spent.cost_micros,
        second.spent.cost_micros,
    )
    spent = (first.spent.cost_micros or 0) + (second.spent.cost_micros or 0)
    total = await ledger.read_usage_total(org, session)
    assert (total.calls, total.cost_micros, total.settled_whole) == (2, spent, 1)
    assert spent > 0 and total.unpriced == 0


async def test_a_record_that_fails_to_land_never_fails_its_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ledger settles each call, then the record's write fails as a
    database that timed out does: the reply is kept, the loop goes on to its
    end, and only the reading is lost."""
    loop = loop_over(tmp_path)
    ledger = loop.storage.get_ledger_storage()

    async def timed_out(org_id: UUID, record: UsageRecord) -> bool:
        raise Unavailable("the database did not answer in time")

    monkeypatch.setattr(ledger, "append_usage_record", timed_out)
    session = await started(loop)
    await loop.say(session, "What is the total?")
    loop.anthropic.add(
        reply(use("lookup", "the total")).model_copy(update={"usage": FIRST}),
        reply(said("12.")).model_copy(update={"usage": SECOND}),
    )

    run = await loop.loops.run(loop.owner, session)

    assert run.outcome is LoopOutcome.SUCCEEDED
    history = await loop.history(session)
    responses = [s for s in history if s.type is StepType.MODEL_RESPONSE]
    assert len(responses) == 2 and responses[-1].as_text() == "12.", "both replies are kept"
    settled = [await settlement_of(loop, s) for s in history if is_request(s)]
    assert [s.bill.kind for s in settled] == ["billed", "billed"]
    assert await records_of(loop, loop.owner, session) == []
