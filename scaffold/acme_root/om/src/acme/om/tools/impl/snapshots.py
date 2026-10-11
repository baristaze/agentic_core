"""Workspace snapshots as the tools keep them: sealed under their session's
key, stored in the snapshots bucket under a hash keyed by that session, and
trusted only by that hash when they are read back.

A snapshot is content: what a workspace's commands wrote. So it is sealed
like the steps they answered, scanned for the values of the secrets the
catalog's tools may have injected before anything of it is kept, and goes
with its session's purge. Each session keeps its own: a child that starts
from its parent's workspace keeps its copy sealed under its own key, so
neither purge nor revocation of one reaches the other (ADR 1027, ADR
1030)."""

from collections.abc import AsyncIterable, Collection
from uuid import UUID

from acme.infra.buckets import BlobNotFound, Buckets, BucketsInterface
from acme.infra.exceptions import InfraException, SecretNotFound
from acme.infra.secrets import SecretsInterface
from acme.infra.transports import secret_held
from acme.infra.workspaces import SnapshotRefused, WorkspaceLost
from acme.om.context import TenantContext
from acme.om.steps.types.header import HASH_SCHEME, WorkspaceSnapshot
from acme.om.tools.manager import KeyedHash
from acme.om.tools.seal import SnapshotSealInterface

BUCKET = Buckets.SNAPSHOTS
CONTENT_TYPE = "application/octet-stream"


def session_prefix(session_id: UUID) -> str:
    """Where a session's snapshots sit in the bucket, under its tenant."""
    return f"agent-sessions/{session_id}/"


def snapshot_key(session_id: UUID, digest: str) -> str:
    """A snapshot's key: its session, then its keyed hash, so a session that
    keeps the same bytes twice stores them once."""
    return session_prefix(session_id) + digest.removeprefix(HASH_SCHEME)


def _unkept(session_id: UUID) -> SnapshotRefused:
    return SnapshotRefused(
        f"agent session {session_id} keeps no content at rest, so it keeps no snapshot"
    )


class SnapshotStore:
    """`secret_names` are the secrets the catalog's tools may have injected
    into a command: a snapshot that holds the value of one is refused."""

    def __init__(
        self,
        buckets: BucketsInterface,
        seal: SnapshotSealInterface,
        keyed_hash: KeyedHash,
        secrets: SecretsInterface,
        secret_names: Collection[str],
        purge_batch: int,
    ) -> None:
        self._buckets = buckets
        self._seal = seal
        self._keyed_hash = keyed_hash
        self._secrets = secrets
        self._secret_names = tuple(sorted(set(secret_names)))
        self._purge_batch = purge_batch

    async def at_rest(self, ctx: TenantContext, session_id: UUID) -> None:
        """Refuses (`SnapshotRefused`) a session that keeps no content at
        rest, before anything of its workspace is taken: no snapshot of it
        is kept, its own or a sub-agent's copy."""
        if not await self._seal.keeps(ctx, session_id):
            raise _unkept(session_id)

    async def keep(
        self,
        ctx: TenantContext,
        session_id: UUID,
        snapshot_id: UUID,
        workspace_id: UUID,
        archive: bytes,
    ) -> WorkspaceSnapshot:
        """`archive` kept as the session's snapshot under `snapshot_id`,
        taken of the workspace under `workspace_id`. Nothing is stored when
        the session keeps no content at rest (`SnapshotRefused`)."""
        digest = HASH_SCHEME + await self._keyed_hash(ctx, session_id, archive)
        sealed = await self._seal.seal(ctx, session_id, digest, archive)
        if sealed is None:
            raise _unkept(session_id)
        key = snapshot_key(session_id, digest)
        await self._buckets.put(
            ctx.org_id, BUCKET, key, sealed, CONTENT_TYPE, deadline=ctx.deadline
        )
        return WorkspaceSnapshot(
            id=snapshot_id, hash=digest, size=len(archive), workspace_id=workspace_id
        )

    async def load(
        self, ctx: TenantContext, session_id: UUID, snapshot: WorkspaceSnapshot
    ) -> bytes:
        """The bytes of a snapshot the session keeps, trusted only once they
        open under its key and hash to what its step names. Anything else
        loses the workspace that would start from it (`WorkspaceLost`)."""
        what = f"snapshot {snapshot.id} of agent session {session_id}"
        try:
            sealed = await self._buckets.get(
                ctx.org_id, BUCKET, snapshot_key(session_id, snapshot.hash), deadline=ctx.deadline
            )
        except InfraException as error:
            if error.code != BlobNotFound.code:
                raise
            raise WorkspaceLost(f"{what} is gone from the store") from error
        try:
            archive = await self._seal.open(ctx, session_id, snapshot.hash, sealed)
        except ValueError as error:
            raise WorkspaceLost(f"{what} does not open: its stored bytes were altered") from error
        if archive is None:
            raise WorkspaceLost(f"{what} was erased with its session's key")
        digest = HASH_SCHEME + await self._keyed_hash(ctx, session_id, archive)
        if digest != snapshot.hash or len(archive) != snapshot.size:
            raise WorkspaceLost(f"{what} does not match its hash")
        return archive

    async def scan(self, ctx: TenantContext, held: AsyncIterable[bytes]) -> None:
        """Refuses a snapshot whose bytes, `held` as its provider reads them
        (`WorkspaceProviderInterface.held`), hold the value of a secret the
        catalog's tools may have injected, in any form redaction matches, and
        names the secret, never its value. A secret the tenant does not hold
        was never given, so it is not looked for."""
        values: dict[str, str] = {}
        for name in self._secret_names:
            try:
                values[name] = await self._secrets.get(ctx.org_id, name, deadline=ctx.deadline)
            except InfraException as error:
                if error.code != SecretNotFound.code:
                    raise
        found = await secret_held(held, values)
        if found is not None:
            raise SnapshotRefused(
                f"the workspace holds the secret {found!r}, so no snapshot is kept"
            )

    async def purge(self, org_id: UUID, session_id: UUID) -> None:
        """Every snapshot the session keeps, a page at a time."""
        prefix = session_prefix(session_id)
        while True:
            keys = await self._buckets.list(org_id, BUCKET, prefix, self._purge_batch)
            for key in keys:
                await self._buckets.delete(org_id, BUCKET, key)
            if len(keys) < self._purge_batch:
                return
