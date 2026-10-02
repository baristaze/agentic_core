"""Sealing, storage modes, and the revocation over the memory roots.

The step storage contract runs over each layer the root wires, so a step
reads back as it was appended whichever way its session stores it. Then
what only the layers hold: the history under the sealing layer holds no
content in the clear, a revoked key leaves every step's shape and none of
its content, a new version seals what comes after it, and a memory-only
session leaves nothing it said at rest."""

import json
from pathlib import Path
from uuid import UUID

import pytest
from contracts.agent_session_storage import make_session
from contracts.doubles import context
from contracts.factories import make_org
from contracts.step_storage import (
    StepStorageContract,
    a_loop,
    make_message,
    make_parked,
)

from acme.infra.impl.local import InfraLocalImpl
from acme.infra.keys.memory import KeyServiceMemoryImpl
from acme.om.base import new_id, utcnow
from acme.om.context import Role, TenantContext
from acme.om.exceptions import KeyRevoked, NotAuthorized, NotFound, PolicyFixed, ValidationFailed
from acme.om.privacy.impl.keys import SessionKeysImpl
from acme.om.privacy.impl.memory_only_steps import StepStorageShapeOnlyImpl
from acme.om.privacy.impl.routed_steps import StepStorageRoutedImpl
from acme.om.privacy.impl.sealed_steps import StepStorageSealedImpl, opened, sealed
from acme.om.privacy.storage.impl.memory import PrivacyStorageMemoryImpl
from acme.om.privacy.types.session_privacy import StorageMode, StoragePolicy
from acme.om.root import Managers, build_managers
from acme.om.steps.storage import StepStorageInterface
from acme.om.steps.storage.impl.memory import StepStorageMemoryImpl
from acme.om.steps.types.content import Children, Content, ContentState
from acme.om.steps.types.step import Step
from acme.om.storage.impl.memory import StorageMemoryImpl

SAID = (
    "the weekly report is missing a total",
    "reading the import log",
    "the total is summed before the import ends",
    "200 lines",
    "plot.png",
)
"""What the loop the contract writes says, and no shape repeats."""

MEMORY_ONLY = StoragePolicy(mode=StorageMode.MEMORY_ONLY, keep_shape=False)
SHAPE_KEPT = StoragePolicy(mode=StorageMode.MEMORY_ONLY, keep_shape=True)


def keys_over(privacy: PrivacyStorageMemoryImpl) -> SessionKeysImpl:
    return SessionKeysImpl(privacy, KeyServiceMemoryImpl())


def shape_of(step: Step) -> dict[str, object]:
    """Everything a step holds but what it says."""
    return step.model_dump(exclude={"content", "children"})


def in_the_clear(steps: list[Step]) -> str:
    """The history as a reader of the store sees it."""
    return json.dumps([step.model_dump(mode="json") for step in steps])


class TestSealedStepStorage(StepStorageContract):
    @pytest.fixture
    def storage(self) -> StepStorageInterface:
        return StepStorageSealedImpl(StepStorageMemoryImpl(), keys_over(PrivacyStorageMemoryImpl()))


class TestRoutedStepStorage(StepStorageContract):
    @pytest.fixture
    def storage(self) -> StepStorageInterface:
        privacy = PrivacyStorageMemoryImpl()
        history = StepStorageMemoryImpl()
        return StepStorageRoutedImpl(
            sealed=StepStorageSealedImpl(history, keys_over(privacy)),
            shape_only=StepStorageShapeOnlyImpl(history),
            transient=StepStorageMemoryImpl(),
            policies=privacy,
        )


class TestShapeOnlyStepStorage(StepStorageContract):
    @pytest.fixture
    def storage(self) -> StepStorageInterface:
        return StepStorageShapeOnlyImpl(StepStorageMemoryImpl())


# The sealing layer.


async def test_the_history_under_the_sealing_layer_holds_no_content_in_the_clear() -> None:
    history, privacy = StepStorageMemoryImpl(), PrivacyStorageMemoryImpl()
    storage = StepStorageSealedImpl(history, keys_over(privacy))
    org, session = new_id(), new_id()
    loop = a_loop(session)
    epoch = await storage.begin_run(org, session)
    appended = await storage.append_steps(org, session, epoch, loop)
    stored = await history.read_steps(org, session, 0, 10)
    assert [shape_of(step) for step in stored] == [shape_of(step) for step in appended]
    said = [step for step in loop if step.content.blocks or step.children != Children()]
    assert [step.content.state for step in stored if step.id in {s.id for s in said}] == [
        ContentState.SEALED
    ] * len(said)
    assert all(step.children == Children() for step in stored)
    reader = in_the_clear(stored)
    assert all(phrase in in_the_clear(loop) for phrase in SAID), "what the loop says"
    assert not [phrase for phrase in SAID if phrase in reader]
    assert await storage.read_steps(org, session, 0, 10) == list(appended)
    ring = await privacy.read_keys(org, session)
    assert [key.version for key in ring.keys] == [1]


async def test_a_step_that_says_nothing_takes_no_key() -> None:
    privacy = PrivacyStorageMemoryImpl()
    storage = StepStorageSealedImpl(StepStorageMemoryImpl(), keys_over(privacy))
    org, session = new_id(), new_id()
    epoch = await storage.begin_run(org, session)
    (mark,) = await storage.append_steps(org, session, epoch, [make_parked(session, new_id())])
    assert mark.content == Content()
    assert (await privacy.read_keys(org, session)).keys == ()


async def test_content_sealed_above_storage_is_refused() -> None:
    storage = StepStorageSealedImpl(StepStorageMemoryImpl(), keys_over(PrivacyStorageMemoryImpl()))
    org, session = new_id(), new_id()
    forged = sealed(org, make_message(session), 1, bytes(32))
    with pytest.raises(ValidationFailed):
        await storage.append_inputs(org, session, [forged])
    assert await storage.read_steps(org, session, 0, 10) == []


def test_a_sealed_step_opens_as_itself_alone() -> None:
    """A blob is bound to its tenant, its session, its step, and its key's
    version: moved anywhere else, or opened under another key, it is
    refused, never read as something."""
    org, session, key = new_id(), new_id(), bytes(range(32))
    step = make_message(session)
    closed = sealed(org, step, 2, key)
    assert opened(org, closed, key) == step
    elsewhere = make_message(session).model_copy(update={"content": closed.content})
    for org_id, other, k in (
        (new_id(), closed, key),
        (org, elsewhere, key),
        (org, closed, bytes(32)),
    ):
        with pytest.raises(ValueError, match="does not open"):
            opened(org_id, other, k)


async def test_a_new_version_seals_what_comes_after_and_leaves_what_came_before() -> None:
    history, privacy = StepStorageMemoryImpl(), PrivacyStorageMemoryImpl()
    keys = keys_over(privacy)
    storage = StepStorageSealedImpl(history, keys)
    org, session = new_id(), new_id()
    (before,) = await storage.append_inputs(org, session, [make_message(session, "first")])
    assert await keys.add_version(org, session) == 2
    (after,) = await storage.append_inputs(org, session, [make_message(session, "second")])
    stored = await history.read_steps(org, session, 0, 10)
    assert [step.content.sealed.version for step in stored if step.content.sealed] == [1, 2]
    assert await storage.read_steps(org, session, 0, 10) == [before, after]
    assert (before.as_text(), after.as_text()) == ("first", "second")


# The manager over the memory roots.


@pytest.fixture
def infra(tmp_path: Path) -> InfraLocalImpl:
    return InfraLocalImpl(tmp_path)


@pytest.fixture
def managers(infra: InfraLocalImpl) -> Managers:
    return build_managers(StorageMemoryImpl(), infra)


async def a_session(managers: Managers, ctx: TenantContext) -> UUID:
    return (await managers.agent_sessions.create_session(ctx, make_session())).id


async def test_a_revoked_key_leaves_every_steps_shape_and_none_of_its_content(
    managers: Managers,
) -> None:
    """The erasure: every step keeps its id, its type, its place, its
    header, and its references, and reads as absent; nothing the session
    said is readable, through the engine or under it. The session takes no
    content again, and still takes a step that says nothing, so its loop
    can end. A second revocation changes nothing, and it is announced
    once."""
    ctx = context(Role.MEMBER)
    session = await a_session(managers, ctx)
    epoch = await managers.steps.begin_run(ctx, session)
    loop = a_loop(session)
    written = await managers.steps.append_steps(ctx, session, epoch, loop)
    before = (await managers.steps.get_steps(ctx, session, 0, 50)).items
    assert before == written

    revoked = await managers.privacy.revoke_key(ctx, session)
    assert revoked.revoked_by == ctx.user_id

    after = (await managers.steps.get_steps(ctx, session, 0, 50)).items
    assert [shape_of(step) for step in after] == [shape_of(step) for step in before]
    said = {step.id for step in loop if step.content.blocks or step.children != Children()}
    for step in after:
        if step.id in said:
            assert step.content == Content(state=ContentState.ABSENT)
            assert step.children == Children()
        else:
            assert step.content == Content()
    reader = in_the_clear(list(after))
    assert not [phrase for phrase in SAID if phrase in reader]

    with pytest.raises(KeyRevoked):
        await managers.steps.append_inputs(ctx, session, [make_message(session)])
    with pytest.raises(KeyRevoked):
        await managers.privacy.keyed_hash(ctx, session, b"input")
    with pytest.raises(KeyRevoked):
        await managers.privacy.rotate_key(ctx, session)
    (closing,) = await managers.steps.append_steps(
        ctx, session, epoch, [make_parked(session, loop[0].id)]
    )
    assert closing.seq == len(loop) + 1


async def test_a_revocation_is_announced_once(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    session = await a_session(managers, ctx)
    await managers.steps.append_inputs(ctx, session, [make_message(session)])
    first = await managers.privacy.revoke_key(ctx, session)
    assert await managers.privacy.revoke_key(ctx, session) == first
    events = await managers.events.get_events(ctx, 0, 100)
    assert [e.kind for e in events if e.target_id == session].count(
        "privacy.session_privacy.updated"
    ) == 1


async def test_a_memory_only_session_keeps_what_it_says_out_of_the_history(
    managers: Managers,
) -> None:
    """With nothing at rest allowed, the history never hears of the
    session; with its shape kept, the history holds each step's shape and
    nothing it said. Either way the engine in this process reads it all."""
    ctx = context(Role.MEMBER)
    for policy in (MEMORY_ONLY, SHAPE_KEPT):
        session = await a_session(managers, ctx)
        chosen = await managers.privacy.set_policy(ctx, session, policy)
        assert chosen.policy == policy
        epoch = await managers.steps.begin_run(ctx, session)
        written = await managers.steps.append_steps(ctx, session, epoch, a_loop(session))
        assert (await managers.steps.get_steps(ctx, session, 0, 50)).items == written


async def test_a_revoked_memory_only_session_answers_nothing_it_said(
    managers: Managers,
) -> None:
    """Its content is under no key, so the revocation is what the engine
    stops answering: what this process holds reads as absent, and the
    session takes no content again, as a sealed one does not."""
    ctx = context(Role.MEMBER)
    for policy in (MEMORY_ONLY, SHAPE_KEPT):
        session = await a_session(managers, ctx)
        await managers.privacy.set_policy(ctx, session, policy)
        epoch = await managers.steps.begin_run(ctx, session)
        loop = a_loop(session)
        written = await managers.steps.append_steps(ctx, session, epoch, loop)
        await managers.privacy.revoke_key(ctx, session)
        after = (await managers.steps.get_steps(ctx, session, 0, 50)).items
        assert [shape_of(step) for step in after] == [shape_of(step) for step in written]
        assert not [phrase for phrase in SAID if phrase in in_the_clear(list(after))]
        with pytest.raises(KeyRevoked):
            await managers.steps.append_inputs(ctx, session, [make_message(session)])
        (mark,) = await managers.steps.append_steps(
            ctx, session, epoch, [make_parked(session, loop[0].id)]
        )
        assert mark.seq == len(loop) + 1


async def test_a_policy_is_chosen_once_before_the_history_begins(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    session = await a_session(managers, ctx)
    chosen = await managers.privacy.set_policy(ctx, session, SHAPE_KEPT)
    assert await managers.privacy.set_policy(ctx, session, SHAPE_KEPT) == chosen
    with pytest.raises(PolicyFixed):
        await managers.privacy.set_policy(ctx, session, StoragePolicy())
    begun = await a_session(managers, ctx)
    await managers.steps.append_inputs(ctx, begun, [make_message(begun)])
    assert (await managers.privacy.get_privacy(ctx, begun)).policy == StoragePolicy()
    with pytest.raises(PolicyFixed):
        await managers.privacy.set_policy(ctx, begun, MEMORY_ONLY)
    running = await a_session(managers, ctx)
    await managers.steps.begin_run(ctx, running)
    with pytest.raises(PolicyFixed):
        await managers.privacy.set_policy(ctx, running, MEMORY_ONLY)


async def test_a_keyed_hash_is_the_sessions_own(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    one, two = await a_session(managers, ctx), await a_session(managers, ctx)
    first = await managers.privacy.keyed_hash(ctx, one, b"lines 1-200")
    assert await managers.privacy.keyed_hash(ctx, one, b"lines 1-200") == first
    assert await managers.privacy.keyed_hash(ctx, two, b"lines 1-200") != first
    assert await managers.privacy.keyed_hash(ctx, one, b"lines 1-201") != first


async def test_privacy_takes_its_permissions_and_its_tenants_sessions(
    managers: Managers,
) -> None:
    org = make_org()
    ctx = context(Role.MEMBER, org)
    session = await a_session(managers, ctx)
    viewer = context(Role.VIEWER, org)
    for call in (
        managers.privacy.revoke_key(viewer, session),
        managers.privacy.set_policy(viewer, session, MEMORY_ONLY),
        managers.privacy.rotate_key(viewer, session),
    ):
        with pytest.raises(NotAuthorized):
            await call
    other = context(Role.OWNER)
    for call in (
        managers.privacy.get_privacy(other, session),
        managers.privacy.revoke_key(other, session),
        managers.privacy.set_policy(other, session, MEMORY_ONLY),
        managers.privacy.rotate_key(other, session),
    ):
        with pytest.raises(NotFound):
            await call
    assert (await managers.privacy.get_privacy(ctx, session)).revoked_at is None


async def test_a_rotation_rewraps_every_key_and_rewrites_no_step(
    managers: Managers, infra: InfraLocalImpl
) -> None:
    ctx = context(Role.MEMBER)
    sessions = [await a_session(managers, ctx) for _ in range(3)]
    for session in sessions:
        await managers.steps.append_inputs(ctx, session, [make_message(session)])
    before = {s: (await managers.steps.get_steps(ctx, s, 0, 10)).items for s in sessions}
    service = infra.get_keys()
    assert isinstance(service, KeyServiceMemoryImpl)
    rotated = utcnow()
    service.rotate(ctx.org_id)
    assert await managers.privacy.rewrap_keys(ctx, rotated) == 3
    assert await managers.privacy.rewrap_keys(ctx, rotated) == 0
    for session in sessions:
        assert (await managers.steps.get_steps(ctx, session, 0, 10)).items == before[session]
