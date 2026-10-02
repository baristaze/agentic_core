"""The step storage contract: the history, its gapless numbers, and the writer
epoch that fences a run's appends. The cases named in `CROSS_TENANT_CASES`
are the tenant fence's evidence: each one presents another tenant's
identifier and asserts that nothing is found and nothing changes."""

from uuid import UUID

import pytest

from acme.om.base import new_id, utcnow
from acme.om.exceptions import StaleWriter, TenantMismatch, UniqueKeyTaken, ValidationFailed
from acme.om.steps.storage import StepStorageInterface
from acme.om.steps.types.content import (
    Attachment,
    Children,
    Content,
    ImageBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from acme.om.steps.types.header import (
    InputHeader,
    ModelRequestHeader,
    ModelResponseHeader,
    Park,
    ParkedHeader,
    ParkReason,
    ToolRequestHeader,
    ToolResponseHeader,
)
from acme.om.steps.types.page import StepCursor
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from contracts.racing import race

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {"append_inputs", "append_steps", "begin_run", "read_cursor", "read_steps"}
)
"""Every method of `StepStorageInterface` that takes a tenant has a case in
this module that presents another tenant's. `test_storage_exceptions.py`
holds the two sets to each other, so a new method arrives with its case."""


def make_message(session_id: UUID, text: str = "the gripper drops the part") -> Step:
    """A principal's message: the first step of its loop."""
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
        content=Content(blocks=(TextBlock(text=text),)),
    )


def make_request(session_id: UUID, loop_id: UUID, carried: tuple[UUID, ...] = ()) -> Step:
    """A model request: it references what it carried, and copies nothing."""
    return Step(
        id=new_id(),
        created_at=utcnow(),
        session_id=session_id,
        loop_id=loop_id,
        type=StepType.MODEL_REQUEST,
        actor=Actor.ENGINE,
        origin=Origin.ENGINE,
        refs=carried,
        header=ModelRequestHeader(role="main"),
    )


def make_response(session_id: UUID, loop_id: UUID, request_id: UUID) -> Step:
    """A model response with text, a tool use, and its thinking."""
    return Step(
        id=new_id(),
        created_at=utcnow(),
        session_id=session_id,
        loop_id=loop_id,
        type=StepType.MODEL_RESPONSE,
        actor=Actor.MODEL,
        origin=Origin.ENGINE,
        responds_to=request_id,
        header=ModelResponseHeader(),
        content=Content(
            blocks=(
                TextBlock(text="reading the controller log"),
                ToolUseBlock(id="call_1", name="read_log", input={"lines": [1, 200]}),
            )
        ),
        children=Children(thinking=(ThinkingBlock(text="the release fires early"),)),
    )


def make_tool_request(session_id: UUID, loop_id: UUID, response_id: UUID) -> Step:
    return Step(
        id=new_id(),
        created_at=utcnow(),
        session_id=session_id,
        loop_id=loop_id,
        type=StepType.TOOL_REQUEST,
        actor=Actor.ENGINE,
        origin=Origin.ENGINE,
        refs=(response_id,),
        header=ToolRequestHeader(tool="read_log", tool_use_id="call_1", input_hash="h:1"),
    )


def make_tool_response(session_id: UUID, loop_id: UUID, request_id: UUID) -> Step:
    """A tool response whose result holds text and an image, by placeholder."""
    plot = Attachment(id=new_id(), name="plot.png", media_type="image/png", size=2048, hash="k:9")
    return Step(
        id=new_id(),
        created_at=utcnow(),
        session_id=session_id,
        loop_id=loop_id,
        type=StepType.TOOL_RESPONSE,
        actor=Actor.ENGINE,
        origin=Origin.ENGINE,
        responds_to=request_id,
        header=ToolResponseHeader(),
        content=Content(
            blocks=(
                ToolResultBlock(
                    tool_use_id="call_1",
                    parts=(TextBlock(text="200 lines"), ImageBlock(attachment_id=plot.id)),
                ),
            )
        ),
        children=Children(attachments=(plot,)),
    )


def make_parked(session_id: UUID, loop_id: UUID) -> Step:
    return Step(
        id=new_id(),
        created_at=utcnow(),
        session_id=session_id,
        loop_id=loop_id,
        type=StepType.PARKED,
        actor=Actor.ENGINE,
        origin=Origin.ENGINE,
        header=ParkedHeader(park=Park(reason=ParkReason.PERSON, unlock="approval")),
    )


def a_loop(session_id: UUID) -> list[Step]:
    """One of each kind a loop writes, each referencing the one before it."""
    message = make_message(session_id)
    request = make_request(session_id, message.id, (message.id,))
    response = make_response(session_id, message.id, request.id)
    call = make_tool_request(session_id, message.id, response.id)
    result = make_tool_response(session_id, message.id, call.id)
    return [message, request, response, call, result, make_parked(session_id, message.id)]


class StepStorageContract:
    @pytest.fixture
    def storage(self) -> StepStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    async def test_append_assigns_a_gapless_seq_per_session(
        self, storage: StepStorageInterface
    ) -> None:
        org, session, other = new_id(), new_id(), new_id()
        epoch = await storage.begin_run(org, session)
        first = await storage.append_steps(org, session, epoch, [make_message(session)])
        loop = first[0].id
        rest = await storage.append_steps(
            org, session, epoch, [make_request(session, loop), make_request(session, loop)]
        )
        appended = [*first, *rest]
        assert [s.seq for s in appended] == [1, 2, 3]
        assert await storage.read_cursor(org, session) == StepCursor(head=3, epoch=epoch)
        elsewhere = await storage.append_steps(
            org, other, await storage.begin_run(org, other), [make_message(other)]
        )
        assert [s.seq for s in elsewhere] == [1]
        assert await storage.read_steps(org, session, 0, 10) == appended
        assert await storage.read_steps(org, session, 1, 10) == appended[1:]
        assert await storage.read_steps(org, session, 0, 2) == appended[:2]
        assert await storage.read_steps(org, session, 3, 10) == []
        assert await storage.read_steps(org, other, 0, 10) == list(elsewhere)

    async def test_a_step_comes_back_as_it_was_appended(
        self, storage: StepStorageInterface
    ) -> None:
        """Every field of every kind a loop writes survives the round trip:
        the header, the blocks, the thinking, the placeholders, and the
        references. Only `seq` is storage's."""
        org, session = new_id(), new_id()
        loop = a_loop(session)
        epoch = await storage.begin_run(org, session)
        appended = await storage.append_steps(org, session, epoch, loop)
        assert list(appended) == [
            step.model_copy(update={"seq": n}) for n, step in enumerate(loop, start=1)
        ]
        assert await storage.read_steps(org, session, 0, 10) == list(appended)

    async def test_begin_run_takes_an_epoch_above_every_before(
        self, storage: StepStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        assert await storage.read_cursor(org, session) == StepCursor(head=0, epoch=0)
        assert [await storage.begin_run(org, session) for _ in range(3)] == [1, 2, 3]
        assert await storage.read_cursor(org, session) == StepCursor(head=0, epoch=3)
        assert await storage.begin_run(org, new_id()) == 1

    async def test_an_append_under_a_stale_epoch_is_refused_and_writes_nothing(
        self, storage: StepStorageInterface
    ) -> None:
        """The run that lost its claim cannot write a step: not one more
        number is taken, and the run that holds the session goes on from
        where the history stands."""
        org, session = new_id(), new_id()
        lost = await storage.begin_run(org, session)
        landed = await storage.append_steps(org, session, lost, [make_message(session)])
        held = await storage.begin_run(org, session)
        assert held > lost
        for stale in (lost, held + 1, 0):
            with pytest.raises(StaleWriter):
                await storage.append_steps(org, session, stale, [make_message(session)])
        assert await storage.read_steps(org, session, 0, 10) == list(landed)
        assert await storage.read_cursor(org, session) == StepCursor(head=1, epoch=held)
        (next_step,) = await storage.append_steps(org, session, held, [make_message(session)])
        assert next_step.seq == 2

    async def test_a_run_that_never_began_is_refused(self, storage: StepStorageInterface) -> None:
        org, session = new_id(), new_id()
        with pytest.raises(StaleWriter):
            await storage.append_steps(org, session, 1, [make_message(session)])
        await storage.append_inputs(org, session, [make_message(session)])
        with pytest.raises(StaleWriter):
            await storage.append_steps(org, session, 1, [make_message(session)])
        assert await storage.read_cursor(org, session) == StepCursor(head=1, epoch=0)

    async def test_the_inbox_appends_inputs_with_no_epoch_on_the_same_numbers(
        self, storage: StepStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        (first,) = await storage.append_inputs(org, session, [make_message(session)])
        assert first.seq == 1
        epoch = await storage.begin_run(org, session)
        (request,) = await storage.append_steps(
            org, session, epoch, [make_request(session, first.id, (first.id,))]
        )
        (steer,) = await storage.append_inputs(org, session, [make_message(session, "not that")])
        assert (request.seq, steer.seq) == (2, 3)
        assert await storage.read_cursor(org, session) == StepCursor(head=3, epoch=epoch)

    async def test_the_inbox_refuses_what_a_run_writes(self, storage: StepStorageInterface) -> None:
        org, session = new_id(), new_id()
        message = make_message(session)
        with pytest.raises(ValidationFailed):
            await storage.append_inputs(
                org, session, [message, make_request(session, message.id, (message.id,))]
            )
        assert await storage.read_steps(org, session, 0, 10) == []
        assert await storage.read_cursor(org, session) == StepCursor()

    async def test_append_is_idempotent_on_the_id(self, storage: StepStorageInterface) -> None:
        org, session = new_id(), new_id()
        epoch = await storage.begin_run(org, session)
        message = make_message(session)
        (first,) = await storage.append_steps(org, session, epoch, [message])
        again = await storage.append_steps(
            org, session, epoch, [message.model_copy(update={"origin": Origin.CLI})]
        )
        assert again == (first,)
        (by_inbox,) = await storage.append_inputs(org, session, [message])
        assert by_inbox == first
        assert await storage.read_cursor(org, session) == StepCursor(head=1, epoch=epoch)

    async def test_a_batch_that_names_an_id_twice_or_another_session_is_refused(
        self, storage: StepStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        epoch = await storage.begin_run(org, session)
        message = make_message(session)
        with pytest.raises(ValidationFailed):
            await storage.append_steps(org, session, epoch, [message, message])
        with pytest.raises(ValidationFailed):
            await storage.append_steps(org, session, epoch, [make_message(new_id())])
        assert await storage.read_cursor(org, session) == StepCursor(head=0, epoch=epoch)

    async def test_an_id_another_session_holds_is_refused_whole(
        self, storage: StepStorageInterface
    ) -> None:
        org, session, other = new_id(), new_id(), new_id()
        held = make_message(other)
        await storage.append_inputs(org, other, [held])
        epoch = await storage.begin_run(org, session)
        with pytest.raises(UniqueKeyTaken):
            await storage.append_steps(
                org,
                session,
                epoch,
                [make_message(session), held.model_copy(update={"session_id": session})],
            )
        assert await storage.read_steps(org, session, 0, 10) == []
        assert await storage.read_cursor(org, session) == StepCursor(head=0, epoch=epoch)

    async def test_an_id_another_tenant_appended_is_never_written_over(
        self, storage: StepStorageInterface
    ) -> None:
        """Ids are unique across tenants, and a repeat from another tenant is
        not the same step: it is refused, both ways in, and spends nothing."""
        org_a, org_b, session = new_id(), new_id(), new_id()
        step = make_message(session)
        (appended,) = await storage.append_inputs(org_a, session, [step])
        stolen = step.model_copy(update={"origin": Origin.API})
        with pytest.raises(TenantMismatch):
            await storage.append_inputs(org_b, session, [stolen])
        epoch = await storage.begin_run(org_b, session)
        with pytest.raises(TenantMismatch):
            await storage.append_steps(org_b, session, epoch, [stolen])
        assert await storage.read_steps(org_a, session, 0, 10) == [appended]
        assert await storage.read_steps(org_b, session, 0, 10) == []
        assert (await storage.read_cursor(org_b, session)).head == 0

    async def test_another_tenant_naming_the_session_reaches_none_of_it(
        self, storage: StepStorageInterface
    ) -> None:
        """A session id is the tenant's: another tenant that names it reads
        no step and no cursor, and the run it begins there takes nothing
        from the holder's, whose appends go on landing."""
        org_a, org_b, session = new_id(), new_id(), new_id()
        epoch = await storage.begin_run(org_a, session)
        (step,) = await storage.append_steps(org_a, session, epoch, [make_message(session)])
        assert await storage.read_steps(org_b, session, 0, 10) == []
        assert await storage.read_cursor(org_b, session) == StepCursor()
        assert await storage.begin_run(org_b, session) == 1
        assert await storage.read_cursor(org_a, session) == StepCursor(head=1, epoch=epoch)
        (later,) = await storage.append_steps(org_a, session, epoch, [make_message(session)])
        assert later.seq == 2
        assert await storage.read_steps(org_a, session, 0, 10) == [step, later]

    async def test_many_appends_never_share_or_skip_a_seq(
        self, storage: StepStorageInterface
    ) -> None:
        """N appends reach one session's cursor at once, a run's and the
        inbox's mixed, and leave with 1..N: no gap, no duplicate, and the head
        is the last of them. See contracts/racing.py for what each impl's run
        of this proves."""
        org, session, n = new_id(), new_id(), 32
        epoch = await storage.begin_run(org, session)

        async def one(by_run: bool) -> Step:
            step = make_message(session)
            if by_run:
                (appended,) = await storage.append_steps(org, session, epoch, [step])
            else:
                (appended,) = await storage.append_inputs(org, session, [step])
            return appended

        run = await race(*(one(i % 2 == 0) for i in range(n)))
        assert sorted(s.seq for s in run.outcomes) == list(range(1, n + 1)), run.summary()
        history = await storage.read_steps(org, session, 0, n * 2)
        assert [s.seq for s in history] == list(range(1, n + 1))
        assert {s.id for s in history} == {s.id for s in run.outcomes}
        assert await storage.read_cursor(org, session) == StepCursor(head=n, epoch=epoch)
        (after,) = await storage.append_steps(org, session, epoch, [make_message(session)])
        assert after.seq == n + 1

    async def test_many_appends_keep_one_cursor_per_session(
        self, storage: StepStorageInterface
    ) -> None:
        org, first, second, n = new_id(), new_id(), new_id(), 16
        epochs = {first: await storage.begin_run(org, first)}
        epochs[second] = await storage.begin_run(org, second)

        async def one(session: UUID) -> Step:
            (appended,) = await storage.append_steps(
                org, session, epochs[session], [make_message(session)]
            )
            return appended

        run = await race(*(one(session) for session in (first, second) * n))
        for session in (first, second):
            seqs = sorted(s.seq for s in run.outcomes if s.session_id == session)
            assert seqs == list(range(1, n + 1)), run.summary()
