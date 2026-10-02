"""The agent session storage contract. The cases named in
`CROSS_TENANT_CASES` are the tenant fence's evidence: each one presents
another tenant's identifier and asserts that nothing is found and nothing
changes."""

from datetime import timedelta

import pytest

from acme.om.agent_sessions.storage import AgentSessionStorageInterface
from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.base import new_id, utcnow
from acme.om.exceptions import PreconditionFailed
from acme.om.steps.types.header import Park, ParkReason

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {
        "create_session",
        "purge_tenant",
        "read_children",
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
        kind="delivery",
        kind_version=1,
        tools=("read_log", "run_tests"),
        parent_id=None if parent is None else parent.id,
        root_id=session_id if parent is None else parent.root_id,
        depth=1 if parent is None else parent.depth + 1,
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

    async def test_what_attribution_reads_round_trips(
        self, storage: AgentSessionStorageInterface
    ) -> None:
        org = new_id()
        session = make_session().model_copy(
            update={
                "speaker": Principal(kind=PrincipalKind.SERVICE, id=new_id()),
                "untrusted": True,
                "handed_off_from": new_id(),
            }
        )
        assert await storage.create_session(org, session, ())
        assert await storage.read_session(org, session.id) == session

    async def test_read_children_by_parent_and_tenant_in_id_order(
        self, storage: AgentSessionStorageInterface
    ) -> None:
        org, elsewhere = new_id(), new_id()
        root = make_session()
        assert await storage.create_session(org, root, ())
        children = sorted((make_session(parent=root) for _ in range(3)), key=lambda s: s.id)
        for child in children:
            assert await storage.create_session(org, child, ())
        grandchild = make_session(parent=children[0])
        assert await storage.create_session(org, grandchild, ())
        assert await storage.read_children(org, root.id, None, 10) == children
        assert await storage.read_children(org, root.id, children[0].id, 1) == [children[1]]
        assert await storage.read_children(org, children[0].id, None, 10) == [grandchild]
        assert await storage.read_children(org, grandchild.id, None, 10) == []
        assert await storage.read_children(elsewhere, root.id, None, 10) == []

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
