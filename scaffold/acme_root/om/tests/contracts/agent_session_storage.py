"""The agent session storage contract. The cases named in
`CROSS_TENANT_CASES` are the tenant fence's evidence: each one presents
another tenant's identifier and asserts that nothing is found and nothing
changes."""

from datetime import datetime, timedelta

import pytest

from acme.om.agent_sessions.storage import AgentSessionStorageInterface
from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.base import new_id, utcnow
from acme.om.exceptions import PreconditionFailed
from acme.om.steps.types.header import Park, ParkReason

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {
        "create_session",
        "purge_session",
        "purge_tenant",
        "read_session",
        "read_sessions",
        "write_session",
    }
)
"""Every method of `AgentSessionStorageInterface` that takes a tenant has a
case in this module that presents another tenant's."""


def make_session(*, parent: AgentSession | None = None) -> AgentSession:
    now = utcnow()
    actor = new_id()
    session_id = new_id()
    return AgentSession(
        id=session_id,
        created_at=now,
        updated_at=now,
        created_by=actor,
        updated_by=actor,
        title="the weekly report is missing a total",
        participants=(actor, new_id()),
        parent_id=None if parent is None else parent.id,
        root_id=session_id if parent is None else parent.root_id,
    )


def parked(session: AgentSession, version: int) -> AgentSession:
    """The session as a projection leaves it when its loop parks."""
    return session.model_copy(
        update={
            "status": SessionStatus.PARKED,
            "park": Park(
                reason=ParkReason.BUDGET, unlock="raise", retry_at=utcnow() + timedelta(hours=1)
            ),
            "status_seq": 7,
            "pending_input": new_id(),
            "delivering_request": new_id(),
            "version": version,
            "updated_at": utcnow(),
        }
    )


def marked(
    session: AgentSession, version: int, at: datetime, *, claimed: bool = False
) -> AgentSession:
    """The session as a delete leaves it, and as the sweep's claim leaves it
    when `claimed`."""
    return session.model_copy(
        update={
            "deleted_at": at,
            "deleted_by": new_id(),
            "purge_started_at": at if claimed else None,
            "version": version,
            "updated_at": at,
        }
    )


class AgentSessionStorageContract:
    @pytest.fixture
    def storage(self) -> AgentSessionStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    async def test_round_trip(self, storage: AgentSessionStorageInterface) -> None:
        org = new_id()
        root = make_session()
        child = make_session(parent=root)
        assert await storage.create_session(org, root, ())
        assert await storage.create_session(org, child, ())
        assert await storage.read_session(org, root.id) == root
        assert await storage.read_session(org, child.id) == child
        assert await storage.read_session(org, new_id()) is None

    async def test_a_title_keeps_what_both_impls_store(
        self, storage: AgentSessionStorageInterface
    ) -> None:
        org = new_id()
        session = AgentSession.model_validate(
            {**make_session().model_dump(), "title": f"a\x00b{chr(0xDFFF)}c"}
        )
        assert session.title == "a\ufffdb\ufffdc"
        assert await storage.create_session(org, session, ())
        assert await storage.read_session(org, session.id) == session

    async def test_purge_tenant_takes_the_tenants_sessions_a_batch_at_a_time(
        self, storage: AgentSessionStorageInterface
    ) -> None:
        gone, kept = new_id(), new_id()
        for _ in range(3):
            assert await storage.create_session(gone, make_session(), ())
        stays = make_session()
        assert await storage.create_session(kept, stays, ())
        assert await storage.purge_tenant(gone, 2) == 2
        assert await storage.purge_tenant(gone, 2) == 1
        assert await storage.purge_tenant(gone, 2) == 0
        assert await storage.read_sessions(gone, None, None, 10) == []
        assert await storage.read_sessions(kept, None, None, 10) == [stays]

    async def test_a_deleted_session_is_on_no_page_and_still_read_by_id(
        self, storage: AgentSessionStorageInterface
    ) -> None:
        """A page leaves a session marked deleted out, in any status or in
        none, so no page holds one the manager hides. The read by id answers
        it as it is, for the manager to hide, unmark, or find claimed, and
        an unmark puts it back on the page."""
        org = new_id()
        kept, gone = make_session(), make_session()
        for session in (kept, gone):
            assert await storage.create_session(org, session, ())
        deleted = marked(gone, 2, utcnow())
        await storage.write_session(org, deleted, 1, ())
        assert await storage.read_sessions(org, None, None, 10) == [kept]
        assert await storage.read_sessions(org, SessionStatus.IDLE, None, 10) == [kept]
        assert await storage.read_session(org, gone.id) == deleted
        restored = deleted.model_copy(update={"deleted_at": None, "deleted_by": None, "version": 3})
        await storage.write_session(org, restored, 2, ())
        page = await storage.read_sessions(org, None, None, 10)
        assert page == sorted([kept, restored], key=lambda session: session.id)

    async def test_read_purgeable_answers_sessions_deleted_before_the_cut_with_their_tenant(
        self, storage: AgentSessionStorageInterface
    ) -> None:
        """The sweep's read across tenants: every session marked deleted
        before the cut, claimed or not, each with its tenant, a batch at most;
        never one marked since the cut, nor one not marked."""
        first, second, now = new_id(), new_id(), utcnow()
        old, older, claimed, recent, live = (make_session() for _ in range(5))
        for org, session in (
            (first, old),
            (first, recent),
            (first, live),
            (second, older),
            (second, claimed),
        ):
            assert await storage.create_session(org, session, ())
        due = {
            (first, old.id): marked(old, 2, now - timedelta(days=31)),
            (second, older.id): marked(older, 2, now - timedelta(days=40)),
            (second, claimed.id): marked(claimed, 2, now - timedelta(days=50), claimed=True),
        }
        for (org, _), session in due.items():
            await storage.write_session(org, session, 1, ())
        await storage.write_session(first, marked(recent, 2, now - timedelta(days=1)), 1, ())
        found = await storage.read_purgeable(now - timedelta(days=30), 10)
        assert {(org, session.id): session for org, session in found} == due
        assert len(await storage.read_purgeable(now - timedelta(days=30), 2)) == 2

    async def test_purge_session_deletes_a_claimed_session_alone(
        self, storage: AgentSessionStorageInterface
    ) -> None:
        """A session's row goes only once the sweep has claimed it: a session
        merely marked deleted stays, and so does a claimed one named under
        another tenant."""
        org, other, now = new_id(), new_id(), utcnow()
        unclaimed, claimed = make_session(), make_session()
        for session in (unclaimed, claimed):
            assert await storage.create_session(org, session, ())
        await storage.write_session(org, marked(unclaimed, 2, now), 1, ())
        purgeable = marked(claimed, 2, now, claimed=True)
        await storage.write_session(org, purgeable, 1, ())
        assert not await storage.purge_session(other, claimed.id), "another tenant's"
        assert not await storage.purge_session(org, unclaimed.id), "marked, never claimed"
        assert await storage.read_session(org, claimed.id) == purgeable
        assert await storage.purge_session(org, claimed.id)
        assert await storage.read_session(org, claimed.id) is None
        assert not await storage.purge_session(org, claimed.id), "gone already"
        assert await storage.read_session(org, unclaimed.id) is not None

    async def test_create_reports_an_existing_id_and_changes_nothing(
        self, storage: AgentSessionStorageInterface
    ) -> None:
        org = new_id()
        session = make_session()
        assert await storage.create_session(org, session, ())
        assert not await storage.create_session(org, session.model_copy(update={"title": "x"}), ())
        assert await storage.read_session(org, session.id) == session

    async def test_create_session_under_another_tenant_is_not_read_here(
        self, storage: AgentSessionStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        session = make_session()
        assert await storage.create_session(org_a, session, ())
        assert not await storage.create_session(
            org_b, session.model_copy(update={"title": "x"}), ()
        )
        assert await storage.read_session(org_b, session.id) is None
        assert await storage.read_session(org_a, session.id) == session

    async def test_read_sessions_by_status_and_tenant_in_id_order(
        self, storage: AgentSessionStorageInterface
    ) -> None:
        org, elsewhere = new_id(), new_id()
        sessions = sorted((make_session() for _ in range(3)), key=lambda s: s.id)
        for session in sessions:
            assert await storage.create_session(org, session, ())
        assert await storage.create_session(elsewhere, make_session(), ())
        waiting = parked(sessions[1], version=2)
        await storage.write_session(org, waiting, 1, ())
        assert await storage.read_sessions(org, None, None, 10) == [
            sessions[0],
            waiting,
            sessions[2],
        ]
        assert await storage.read_sessions(org, None, sessions[0].id, 1) == [waiting]
        assert await storage.read_sessions(org, SessionStatus.PARKED, None, 10) == [waiting]
        assert await storage.read_sessions(org, SessionStatus.IDLE, None, 10) == [
            sessions[0],
            sessions[2],
        ]
        assert await storage.read_sessions(new_id(), None, None, 10) == []

    async def test_write_is_a_compare_and_set_on_the_version(
        self, storage: AgentSessionStorageInterface
    ) -> None:
        org = new_id()
        session = make_session()
        assert await storage.create_session(org, session, ())
        moved = parked(session, version=2)
        await storage.write_session(org, moved, 1, ())
        assert await storage.read_session(org, session.id) == moved
        with pytest.raises(PreconditionFailed):
            await storage.write_session(org, parked(session, version=2), 1, ())
        assert await storage.read_session(org, session.id) == moved

    async def test_write_session_under_another_tenant_lands_nothing(
        self, storage: AgentSessionStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        session = make_session()
        assert await storage.create_session(org_a, session, ())
        with pytest.raises(PreconditionFailed):
            await storage.write_session(org_b, parked(session, version=2), 1, ())
        assert await storage.read_session(org_a, session.id) == session
        assert await storage.read_sessions(org_b, None, None, 10) == []
