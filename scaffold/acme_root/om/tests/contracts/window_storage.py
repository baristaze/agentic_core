"""The window storage contract: the record of each tool output, written once,
and taken only by the purge, with its session's history or its tenant's.
The cases named in `CROSS_TENANT_CASES` are the tenant fence's evidence:
each one presents another tenant's identifier and asserts that nothing is
found and nothing changes."""

from uuid import UUID

import pytest

from acme.om.base import new_id, utcnow
from acme.om.windows.storage import WindowStorageInterface
from acme.om.windows.types.tool_output import ToolOutput

INTERFACE = WindowStorageInterface
"""The storage interface whose cross-tenant cases this suite holds."""

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {"read_tool_output", "write_tool_output", "read_tool_outputs", "purge_tool_outputs"}
)
"""Every method of `WindowStorageInterface` that takes a tenant has a case
in this module that presents another tenant's."""


def make_tool_output(agent_session_id: UUID, characters: int = 30_000) -> ToolOutput:
    return ToolOutput(
        id=new_id(),
        created_at=utcnow(),
        agent_session_id=agent_session_id,
        agent_step_id=new_id(),
        characters=characters,
    )


class WindowStorageContract:
    @pytest.fixture
    def storage(self) -> WindowStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    async def test_an_tool_output_is_read_back_as_written(
        self, storage: WindowStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        tool_output = make_tool_output(session)
        assert await storage.write_tool_output(org, tool_output) is True
        assert await storage.read_tool_output(org, session, tool_output.id) == tool_output

    async def test_an_tool_output_is_written_once(self, storage: WindowStorageInterface) -> None:
        org, session = new_id(), new_id()
        tool_output = make_tool_output(session)
        await storage.write_tool_output(org, tool_output)
        again = tool_output.model_copy(update={"characters": 1})
        assert await storage.write_tool_output(org, again) is False
        assert await storage.read_tool_output(org, session, tool_output.id) == tool_output

    async def test_another_sessions_tool_output_is_not_read(
        self, storage: WindowStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        tool_output = make_tool_output(session)
        await storage.write_tool_output(org, tool_output)
        assert await storage.read_tool_output(org, new_id(), tool_output.id) is None
        assert await storage.read_tool_output(org, session, new_id()) is None

    async def test_read_tool_output_finds_nothing_of_another_tenant(
        self, storage: WindowStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        tool_output = make_tool_output(session)
        await storage.write_tool_output(org, tool_output)
        assert await storage.read_tool_output(new_id(), session, tool_output.id) is None

    async def test_write_tool_output_under_another_tenant_changes_nothing(
        self, storage: WindowStorageInterface
    ) -> None:
        org, other, session = new_id(), new_id(), new_id()
        tool_output = make_tool_output(session)
        await storage.write_tool_output(org, tool_output)
        assert await storage.write_tool_output(other, tool_output) is False
        assert await storage.read_tool_output(other, session, tool_output.id) is None
        assert await storage.read_tool_output(org, session, tool_output.id) == tool_output

    async def test_a_purge_reads_a_sessions_tool_outputs_or_a_tenants_in_id_order(
        self, storage: WindowStorageInterface
    ) -> None:
        org, session, other = new_id(), new_id(), new_id()
        mine = [make_tool_output(session) for _ in range(3)]
        theirs = make_tool_output(other)
        for tool_output in (*mine, theirs):
            await storage.write_tool_output(org, tool_output)
        assert await storage.read_tool_outputs(org, session, 10) == sorted(mine, key=_id)
        assert await storage.read_tool_outputs(org, session, 2) == sorted(mine, key=_id)[:2]
        every = await storage.read_tool_outputs(org, None, 10)
        assert every == sorted([*mine, theirs], key=_id)

    async def test_read_tool_outputs_finds_nothing_of_another_tenant(
        self, storage: WindowStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        await storage.write_tool_output(org, make_tool_output(session))
        assert await storage.read_tool_outputs(new_id(), session, 10) == []
        assert await storage.read_tool_outputs(new_id(), None, 10) == []

    async def test_a_purge_takes_the_records_it_names_and_no_other(
        self, storage: WindowStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        gone, kept = make_tool_output(session), make_tool_output(session)
        await storage.write_tool_output(org, gone)
        await storage.write_tool_output(org, kept)
        assert await storage.purge_tool_outputs(org, [gone.id, new_id()]) == 1
        assert await storage.read_tool_output(org, session, gone.id) is None
        assert await storage.read_tool_output(org, session, kept.id) == kept
        assert await storage.purge_tool_outputs(org, [gone.id]) == 0
        assert await storage.purge_tool_outputs(org, []) == 0

    async def test_purge_tool_outputs_under_another_tenant_changes_nothing(
        self, storage: WindowStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        tool_output = make_tool_output(session)
        await storage.write_tool_output(org, tool_output)
        assert await storage.purge_tool_outputs(new_id(), [tool_output.id]) == 0
        assert await storage.read_tool_output(org, session, tool_output.id) == tool_output


def _id(tool_output: ToolOutput) -> UUID:
    return tool_output.id
