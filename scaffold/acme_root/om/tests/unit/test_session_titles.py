"""A session's title is content (ADR 1042). Over the memory roots: what
storage keeps of a title holds none of its words, a read answers it sealed
and the manager opens it, a revocation leaves it absent and the session in
its place, a page of titles opens with one read of their keys and one
unwrap whatever its size, another tenant opens none of them, and the sweep
seals in place a title stored in the clear before titles were sealed."""

import base64
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from acme.infra.impl.local import InfraLocalImpl
from acme.infra.keys import KeyRefused, WrappedKey
from acme.infra.keys.memory import KeyServiceMemoryImpl
from acme.om.agent_root import AgentRootOptions
from acme.om.agent_sessions.storage.impl.memory import AgentSessionStorageMemoryImpl
from acme.om.agent_sessions.types.agent_session import AgentSession, Among, Title
from acme.om.agent_steps.types.content import ContentState
from acme.om.context import Role, TenantContext
from acme.om.exceptions import ValidationFailed
from acme.om.privacy.impl.keys import TENANT_TITLE_KEY_VERSION
from acme.om.privacy.storage.impl.memory import PrivacyStorageMemoryImpl
from acme.om.privacy.types.agent_session_privacy import StorageMode, StoragePolicy
from acme.om.root import Managers, build_managers
from acme.om.storage.impl.memory import StorageMemoryImpl

from contracts.agent_session_storage import make_agent_session
from contracts.agent_step_storage import make_message
from contracts.doubles import context
from contracts.factories import make_org
from contracts.tools import stand_ins

TOOLS = stand_ins("read_log", "run_tests")
MARKER = "zq-title-marker-7f3a"
"""Words no shape repeats, planted in a title."""


def encodings(text: str) -> list[bytes]:
    """The marker as it is, and as base64 of any alignment, in either
    alphabet: what a value holding it in the clear would show."""
    raw = text.encode()
    found = [raw]
    for pad in range(3):
        encoded = base64.b64encode(b"\0" * pad + raw)
        piece = encoded[-(-8 * pad // 6) : (8 * (pad + len(raw))) // 6]
        found += [piece, piece.replace(b"+", b"-").replace(b"/", b"_")]
    return found


def values(dumped: Any) -> list[bytes]:
    """Every string and blob a dumped entity holds, as bytes."""
    if isinstance(dumped, dict):
        return [v for item in dumped.values() for v in values(item)]
    if isinstance(dumped, list | tuple):
        return [v for item in dumped for v in values(item)]
    if isinstance(dumped, str):
        return [dumped.encode()]
    if isinstance(dumped, bytes):
        return [dumped]
    return []


def at_rest(storage: StorageMemoryImpl) -> list[bytes]:
    """What a reader of the sessions' and the privacy namespace's storage
    sees: every value of every row they keep."""
    sessions = storage.get_agent_session_storage()
    privacy = storage.get_privacy_storage()
    assert isinstance(sessions, AgentSessionStorageMemoryImpl)
    assert isinstance(privacy, PrivacyStorageMemoryImpl)
    rows: list[Any] = [row for _, row in sessions._sessions.values()]
    rows += [
        row
        for table in (privacy._records, privacy._keys, privacy._title_keys)
        for _, row in table.values()
    ]
    return [value for row in rows for value in values(row.model_dump())]


def holds(found: list[bytes], text: str) -> bool:
    return any(piece in value for value in found for piece in encodings(text))


@pytest.fixture
def storage() -> StorageMemoryImpl:
    return StorageMemoryImpl()


@pytest.fixture
def infra(tmp_path: Path) -> InfraLocalImpl:
    return InfraLocalImpl(tmp_path)


@pytest.fixture
def managers(storage: StorageMemoryImpl, infra: InfraLocalImpl) -> Managers:
    return build_managers(storage, infra, agent_root_options=AgentRootOptions(tool_catalog=TOOLS))


def titled(text: str) -> AgentSession:
    return make_agent_session().model_copy(update={"title": Title(text=text)})


def counted(infra: InfraLocalImpl, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Each call the key service takes, by its name, from now on."""
    service = infra.get_keys()
    calls: list[str] = []
    for name in ("generate", "unwrap", "rewrap"):
        original = getattr(service, name)

        async def call(*args: Any, _name: str = name, _original: Any = original) -> Any:
            calls.append(_name)
            return await _original(*args)

        monkeypatch.setattr(service, name, call)
    return calls


async def test_a_title_holds_none_of_its_words_at_rest_and_opens_for_its_reader(
    managers: Managers, storage: StorageMemoryImpl
) -> None:
    ctx = context(Role.MEMBER)
    made = await managers.agent_sessions.create_agent_session(ctx, titled(f"{MARKER} the total"))
    assert made.title.state is ContentState.SEALED, "the create answers it as stored"
    assert not holds(at_rest(storage), MARKER)
    read = await managers.agent_sessions.get_agent_session(ctx, made.id)
    assert read.title == made.title
    (opened,) = await managers.agent_sessions.open_titles(ctx, [read])
    assert opened.title == Title(text=f"{MARKER} the total")
    assert opened.model_copy(update={"title": read.title}) == read, "the title alone opens"


async def test_a_revoked_sessions_title_reads_absent_and_the_session_keeps_its_place(
    managers: Managers, storage: StorageMemoryImpl
) -> None:
    """The revocation clears the title key in the commit that destroys the
    data keys: the title reads as absent at once, and the list still holds
    the session, by its id, its kind, and its place."""
    ctx = context(Role.MEMBER)
    made = [
        await managers.agent_sessions.create_agent_session(ctx, titled(f"title {n}"))
        for n in range(3)
    ]
    gone = made[1].id
    await managers.agent_steps.append_inputs(ctx, gone, [make_message(gone)])
    await managers.privacy.revoke_key(ctx, gone)
    page = await managers.agent_sessions.get_agent_sessions(ctx, None, None, 50, Among.VISIBLE)
    shown = await managers.agent_sessions.open_titles(ctx, page.items)
    assert [(s.id, s.kind) for s in shown] == [
        (s.id, s.kind) for s in sorted(made, key=lambda s: s.id)
    ]
    assert {s.id: s.title.said() for s in shown} == {
        made[0].id: "title 0",
        gone: "",
        made[2].id: "title 2",
    }
    assert next(s for s in shown if s.id == gone).title.state is ContentState.ABSENT
    record = await storage.get_privacy_storage().read_privacy(ctx.org_id, gone)
    assert record is not None and record.revoked_at is not None and record.title_key is None
    ring = await storage.get_privacy_storage().read_keys(ctx.org_id, gone)
    assert ring.keys and all(key.is_destroyed() for key in ring.keys)


async def test_a_page_of_titles_opens_with_one_unwrap_whatever_its_size(
    managers: Managers,
    storage: StorageMemoryImpl,
    infra: InfraLocalImpl,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One read of the page's title keys and one unwrap of the tenant's
    title key, for a page of one or of fifty; and nothing keeps the tenant's
    key after the call: the next page unwraps it again."""
    ctx = context(Role.MEMBER)
    for n in range(50):
        await managers.agent_sessions.create_agent_session(ctx, titled(f"title {n}"))
    privacy = storage.get_privacy_storage()
    reads: list[int] = []
    read_title_keys = privacy.read_title_keys

    async def read(org_id: UUID, ids: Any) -> Any:
        reads.append(len(ids))
        return await read_title_keys(org_id, ids)

    monkeypatch.setattr(privacy, "read_title_keys", read)
    calls = counted(infra, monkeypatch)
    page = await managers.agent_sessions.get_agent_sessions(ctx, None, None, 50, Among.VISIBLE)
    assert len(page.items) == 50
    for items in (page.items[:1], page.items, page.items):
        before = len(calls)
        opened = await managers.agent_sessions.open_titles(ctx, items)
        assert all(s.title.state is ContentState.PLAIN for s in opened)
        assert calls[before:] == ["unwrap"]
    assert reads == [1, 50, 50]


async def test_another_tenant_opens_no_title_of_the_first(
    managers: Managers, storage: StorageMemoryImpl, infra: InfraLocalImpl
) -> None:
    """The title keys are read by the tenant that asks, and the tenant's
    title key is bound to its tenant at the key service: another tenant
    that holds the first's sessions opens none of their titles, and its call
    cannot unwrap the first's key."""
    owner, other = context(Role.MEMBER), context(Role.MEMBER, make_org("Elsewhere"))
    made = await managers.agent_sessions.create_agent_session(owner, titled(MARKER))
    (stranger,) = await managers.agent_sessions.open_titles(other, [made])
    assert stranger.title.state is ContentState.ABSENT
    found = await storage.get_privacy_storage().read_title_keys(owner.org_id, [made.id])
    assert found.tenant is not None
    service = infra.get_keys()
    assert isinstance(service, KeyServiceMemoryImpl)
    wrapped = WrappedKey(blob=found.tenant.wrapped, wrapping=found.tenant.wrapping)
    await service.unwrap(owner.org_id, found.tenant.id, TENANT_TITLE_KEY_VERSION, wrapped)
    with pytest.raises(KeyRefused):
        await service.unwrap(other.org_id, found.tenant.id, TENANT_TITLE_KEY_VERSION, wrapped)


async def test_a_sealed_title_moved_to_another_session_opens_nothing(
    managers: Managers,
) -> None:
    ctx = context(Role.MEMBER)
    first = await managers.agent_sessions.create_agent_session(ctx, titled("the first"))
    second = await managers.agent_sessions.create_agent_session(ctx, titled("the second"))
    moved = second.model_copy(update={"title": first.title})
    with pytest.raises(ValueError, match="does not open"):
        await managers.agent_sessions.open_titles(ctx, [moved])


async def test_a_create_given_a_sealed_title_is_a_retry_or_refused(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    made = await managers.agent_sessions.create_agent_session(ctx, titled("once"))
    assert await managers.agent_sessions.create_agent_session(ctx, made) == made
    with pytest.raises(ValidationFailed):
        await managers.agent_sessions.create_agent_session(
            ctx, make_agent_session().model_copy(update={"title": made.title})
        )


async def test_a_policy_is_chosen_after_the_title_wrote_the_record(
    managers: Managers, storage: StorageMemoryImpl
) -> None:
    """The title writes the session's record with the default, not chosen:
    a read of the empty history keeps no route, and the policy chosen next
    holds for the history that follows."""
    ctx = context(Role.MEMBER)
    made = await managers.agent_sessions.create_agent_session(ctx, titled("chosen later"))
    record = await storage.get_privacy_storage().read_privacy(ctx.org_id, made.id)
    assert record is not None and not record.chosen and record.title_key is not None
    assert (await managers.agent_steps.get_agent_steps(ctx, made.id, 0, 10)).items == ()
    memory_only = StoragePolicy(mode=StorageMode.MEMORY_ONLY, keep_shape=False)
    chosen = await managers.privacy.set_policy(ctx, made.id, memory_only)
    assert chosen.policy == memory_only and chosen.title_key == record.title_key
    await managers.agent_steps.append_inputs(ctx, made.id, [make_message(made.id)])
    assert await storage.get_agent_step_storage().read_agent_steps(ctx.org_id, made.id, 0, 10) == []


def planted(storage: StorageMemoryImpl, ctx: TenantContext, text: str) -> AgentSession:
    """A session as the release before titles were sealed wrote it: its
    title in the clear."""
    sessions = storage.get_agent_session_storage()
    assert isinstance(sessions, AgentSessionStorageMemoryImpl)
    session = titled(text).model_copy(update={"created_by": ctx.user_id})
    sessions._sessions[session.id] = (ctx.org_id, session)
    return session


async def test_the_sweep_seals_each_title_in_the_clear_in_place(
    managers: Managers, storage: StorageMemoryImpl
) -> None:
    """Each tenant's title reads back the same once sealed, no row holds it
    in the clear, a revoked session's title goes as its revocation owed it,
    and a pass that finds none takes none."""
    first, second = context(Role.MEMBER), context(Role.MEMBER, make_org("Second"))
    kept = [planted(storage, ctx, f"{MARKER} {n}") for n, ctx in enumerate((first, second, first))]
    revoked = planted(storage, second, f"{MARKER} revoked")
    await managers.privacy.revoke_key(second, revoked.id)
    assert holds(at_rest(storage), MARKER)
    assert await managers.agent_sessions.seal_titles_across_tenants() == 4
    assert not holds(at_rest(storage), MARKER)
    for n, (session, ctx) in enumerate(zip(kept, (first, second, first), strict=True)):
        read = await managers.agent_sessions.get_agent_session(ctx, session.id)
        assert read.title.state is ContentState.SEALED and read.version == session.version + 1
        (opened,) = await managers.agent_sessions.open_titles(ctx, [read])
        assert opened.title.said() == f"{MARKER} {n}"
    gone = await managers.agent_sessions.get_agent_session(second, revoked.id)
    assert gone.title.state is ContentState.ABSENT
    assert await managers.agent_sessions.seal_titles_across_tenants() == 0


async def test_a_sweep_that_fails_part_way_keeps_what_it_sealed_and_the_rest_readable(
    managers: Managers,
    storage: StorageMemoryImpl,
    infra: InfraLocalImpl,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tenant whose key service refuses keeps its titles in the clear, and
    readable as they were; every other tenant's are sealed; the next pass
    seals the rest."""
    first, second = context(Role.MEMBER), context(Role.MEMBER, make_org("Second"))
    sealed_first = planted(storage, first, "the first's")
    clear_second = planted(storage, second, "the second's")
    service = infra.get_keys()
    generate = service.generate

    async def refused(org_id: UUID, key_id: UUID, version: int) -> Any:
        if org_id == second.org_id:
            raise RuntimeError("the key service is out")
        return await generate(org_id, key_id, version)

    monkeypatch.setattr(service, "generate", refused)
    assert await managers.agent_sessions.seal_titles_across_tenants() == 2
    read = await managers.agent_sessions.get_agent_session(first, sealed_first.id)
    assert read.title.state is ContentState.SEALED
    still = await managers.agent_sessions.get_agent_session(second, clear_second.id)
    (shown,) = await managers.agent_sessions.open_titles(second, [still])
    assert still.title == shown.title == Title(text="the second's")
    monkeypatch.setattr(service, "generate", generate)
    assert await managers.agent_sessions.seal_titles_across_tenants() == 1
    resealed = await managers.agent_sessions.get_agent_session(second, clear_second.id)
    (opened,) = await managers.agent_sessions.open_titles(second, [resealed])
    assert resealed.title.state is ContentState.SEALED and opened.title.said() == "the second's"
