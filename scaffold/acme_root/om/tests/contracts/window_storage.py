"""The window storage contract: the record of each artifact, written once,
and taken only by the purge, with its session's history or its tenant's.
The cases named in `CROSS_TENANT_CASES` are the tenant fence's evidence:
each one presents another tenant's identifier and asserts that nothing is
found and nothing changes."""

from uuid import UUID

import pytest

from acme.om.base import new_id, utcnow
from acme.om.windows.storage import WindowStorageInterface
from acme.om.windows.types.artifact import Artifact

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {"read_artifact", "write_artifact", "read_artifacts", "purge_artifacts"}
)
"""Every method of `WindowStorageInterface` that takes a tenant has a case
in this module that presents another tenant's."""


def make_artifact(session_id: UUID, characters: int = 30_000) -> Artifact:
    return Artifact(
        id=new_id(),
        created_at=utcnow(),
        session_id=session_id,
        step_id=new_id(),
        characters=characters,
    )


class WindowStorageContract:
    @pytest.fixture
    def storage(self) -> WindowStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    async def test_an_artifact_is_read_back_as_written(
        self, storage: WindowStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        artifact = make_artifact(session)
        assert await storage.write_artifact(org, artifact) is True
        assert await storage.read_artifact(org, session, artifact.id) == artifact

    async def test_an_artifact_is_written_once(self, storage: WindowStorageInterface) -> None:
        org, session = new_id(), new_id()
        artifact = make_artifact(session)
        await storage.write_artifact(org, artifact)
        again = artifact.model_copy(update={"characters": 1})
        assert await storage.write_artifact(org, again) is False
        assert await storage.read_artifact(org, session, artifact.id) == artifact

    async def test_another_sessions_artifact_is_not_read(
        self, storage: WindowStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        artifact = make_artifact(session)
        await storage.write_artifact(org, artifact)
        assert await storage.read_artifact(org, new_id(), artifact.id) is None
        assert await storage.read_artifact(org, session, new_id()) is None

    async def test_read_artifact_finds_nothing_of_another_tenant(
        self, storage: WindowStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        artifact = make_artifact(session)
        await storage.write_artifact(org, artifact)
        assert await storage.read_artifact(new_id(), session, artifact.id) is None

    async def test_write_artifact_under_another_tenant_changes_nothing(
        self, storage: WindowStorageInterface
    ) -> None:
        org, other, session = new_id(), new_id(), new_id()
        artifact = make_artifact(session)
        await storage.write_artifact(org, artifact)
        assert await storage.write_artifact(other, artifact) is False
        assert await storage.read_artifact(other, session, artifact.id) is None
        assert await storage.read_artifact(org, session, artifact.id) == artifact

    async def test_a_purge_reads_a_sessions_artifacts_or_a_tenants_in_id_order(
        self, storage: WindowStorageInterface
    ) -> None:
        org, session, other = new_id(), new_id(), new_id()
        mine = [make_artifact(session) for _ in range(3)]
        theirs = make_artifact(other)
        for artifact in (*mine, theirs):
            await storage.write_artifact(org, artifact)
        assert await storage.read_artifacts(org, session, 10) == sorted(mine, key=_id)
        assert await storage.read_artifacts(org, session, 2) == sorted(mine, key=_id)[:2]
        every = await storage.read_artifacts(org, None, 10)
        assert every == sorted([*mine, theirs], key=_id)

    async def test_read_artifacts_finds_nothing_of_another_tenant(
        self, storage: WindowStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        await storage.write_artifact(org, make_artifact(session))
        assert await storage.read_artifacts(new_id(), session, 10) == []
        assert await storage.read_artifacts(new_id(), None, 10) == []

    async def test_a_purge_takes_the_records_it_names_and_no_other(
        self, storage: WindowStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        gone, kept = make_artifact(session), make_artifact(session)
        await storage.write_artifact(org, gone)
        await storage.write_artifact(org, kept)
        assert await storage.purge_artifacts(org, [gone.id, new_id()]) == 1
        assert await storage.read_artifact(org, session, gone.id) is None
        assert await storage.read_artifact(org, session, kept.id) == kept
        assert await storage.purge_artifacts(org, [gone.id]) == 0
        assert await storage.purge_artifacts(org, []) == 0

    async def test_purge_artifacts_under_another_tenant_changes_nothing(
        self, storage: WindowStorageInterface
    ) -> None:
        org, session = new_id(), new_id()
        artifact = make_artifact(session)
        await storage.write_artifact(org, artifact)
        assert await storage.purge_artifacts(new_id(), [artifact.id]) == 0
        assert await storage.read_artifact(org, session, artifact.id) == artifact


def _id(artifact: Artifact) -> UUID:
    return artifact.id
