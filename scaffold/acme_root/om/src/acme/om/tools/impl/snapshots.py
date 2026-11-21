"""Workspace snapshots as the tools keep them: sealed under their session's
key, stored in the snapshots bucket under a hash keyed by that session, and
trusted only by that hash when they are read back.

A snapshot of a workspace's directory alone is kept in parts: each part
sealed under the session's key on its own, bound to its own keyed hash, and
put under the session's names, then a manifest that names each part by that
hash, kept as any snapshot is. Its size is held to a bound before any part
is read, a store that fails part way removes every part it put, and a
restore holds each part to its hash as it reads it (ADR 1045).

A snapshot is content: what a workspace's commands wrote. So it is sealed
like the steps they answered, scanned for the values of the secrets the
catalog's tools, and the session's own beyond them, may have injected
before anything of it is kept, and goes with its session's purge. Each session keeps its own: a child that starts
from its parent's workspace keeps its copy sealed under its own key, so
neither purge nor revocation of one reaches the other (ADR 1027, ADR
1030)."""

import contextlib
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator, Callable, Collection
from dataclasses import dataclass
from uuid import UUID

from acme.infra.agent_workspaces import AgentWorkspaceLost, SnapshotRefused
from acme.infra.buckets import BlobNotFound, Buckets, BucketsInterface
from acme.infra.exceptions import InfraException, SecretNotFound
from acme.infra.secrets import SecretsInterface
from acme.infra.transports import secret_held
from acme.om.agent_steps.types.header import (
    HASH_SCHEME,
    SNAPSHOT_HASH,
    AgentWorkspaceSnapshot,
    SnapshotHolds,
)
from acme.om.context import TenantContext
from acme.om.tools.manager import KeyedHash
from acme.om.tools.seal import SnapshotSealInterface

log = logging.getLogger(__name__)

BUCKET = Buckets.SNAPSHOTS
CONTENT_TYPE = "application/octet-stream"
DIRECTORY_FORMAT = "acme.directory-snapshot/1"
"""What the manifest of a directory's snapshot says it is."""


def session_prefix(agent_session_id: UUID) -> str:
    """Where a session's snapshots sit in the bucket, under its tenant."""
    return f"agent-sessions/{agent_session_id}/"


def snapshot_key(agent_session_id: UUID, digest: str) -> str:
    """A snapshot's key: its session, then its keyed hash, so a session that
    keeps the same bytes twice stores them once."""
    return session_prefix(agent_session_id) + digest.removeprefix(HASH_SCHEME)


def part_key(agent_session_id: UUID, snapshot_id: UUID, index: int) -> str:
    """Where a part of a directory's snapshot sits: under its session's
    snapshots, so the session's purge finds it, by the snapshot's id and its
    place in the archive."""
    return f"{session_prefix(agent_session_id)}workspace/{snapshot_id}/part-{index:04d}"


@dataclass(frozen=True)
class Part:
    """A part as its manifest names it: its keyed hash, and its size."""

    hash: str
    size: int


@dataclass(frozen=True)
class Manifest:
    """What a directory's snapshot keeps under its hash: the id its parts are
    kept under, the archive's size, and each part, in order."""

    snapshot_id: UUID
    size: int
    parts: tuple[Part, ...]


def _manifest(manifest: Manifest) -> bytes:
    named = {
        "format": DIRECTORY_FORMAT,
        "snapshot": str(manifest.snapshot_id),
        "size": manifest.size,
        "parts": [[part.hash, part.size] for part in manifest.parts],
    }
    return json.dumps(named, sort_keys=True).encode()


def _opened(data: bytes, what: str) -> Manifest:
    """What `_manifest` wrote; anything else loses the workspace that would
    start from it."""
    try:
        named = json.loads(data)
        format_, size = named["format"], named["size"]
        snapshot_id = UUID(named["snapshot"])
        parts = tuple(Part(hash=digest, size=part) for digest, part in named["parts"])
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise AgentWorkspaceLost(f"{what} names no directory's parts: {error}") from error
    if format_ != DIRECTORY_FORMAT:
        raise AgentWorkspaceLost(f"{what} is {format_!r}, not a directory's")
    if not all(
        isinstance(part.hash, str)
        and SNAPSHOT_HASH.match(part.hash)
        and isinstance(part.size, int)
        and part.size > 0
        for part in parts
    ):
        raise AgentWorkspaceLost(f"{what} names a part by no hash or size")
    if not isinstance(size, int) or sum(part.size for part in parts) != size:
        raise AgentWorkspaceLost(f"{what} names parts that do not add up to its size")
    return Manifest(snapshot_id=snapshot_id, size=size, parts=parts)


def _unkept(agent_session_id: UUID) -> SnapshotRefused:
    return SnapshotRefused(
        f"agent session {agent_session_id} keeps no content at rest, so it keeps no snapshot"
    )


class SnapshotStore:
    """`secret_names` are the secrets the catalog's tools may have injected
    into a command: a snapshot that holds the value of one is refused.
    `directory_bound` is the most bytes a directory's archive may hold, and
    `part_bytes` the size of each of its parts but the last."""

    def __init__(
        self,
        buckets: BucketsInterface,
        seal: SnapshotSealInterface,
        keyed_hash: KeyedHash,
        secrets: SecretsInterface,
        secret_names: Collection[str],
        purge_batch: int,
        *,
        directory_bound: int,
        part_bytes: int,
    ) -> None:
        if part_bytes <= 0:
            raise ValueError("a part holds at least one byte")
        self._buckets = buckets
        self._seal = seal
        self._keyed_hash = keyed_hash
        self._secrets = secrets
        self._secret_names = tuple(sorted(set(secret_names)))
        self._purge_batch = purge_batch
        self.directory_bound = directory_bound
        self.part_bytes = part_bytes

    async def at_rest(self, ctx: TenantContext, agent_session_id: UUID) -> None:
        """Refuses (`SnapshotRefused`) a session that keeps no content at
        rest, before anything of its workspace is taken: no snapshot of it
        is kept, its own or a sub-agent's copy."""
        if not await self._seal.keeps(ctx, agent_session_id):
            raise _unkept(agent_session_id)

    async def keep(
        self,
        ctx: TenantContext,
        agent_session_id: UUID,
        snapshot_id: UUID,
        agent_workspace_id: UUID,
        archive: bytes,
    ) -> AgentWorkspaceSnapshot:
        """`archive` kept as the session's snapshot under `snapshot_id`,
        taken of the workspace under `agent_workspace_id`. Nothing is stored when
        the session keeps no content at rest (`SnapshotRefused`)."""
        digest = HASH_SCHEME + await self._keyed_hash(ctx, agent_session_id, archive)
        sealed = await self._seal.seal(ctx, agent_session_id, digest, archive)
        if sealed is None:
            raise _unkept(agent_session_id)
        key = snapshot_key(agent_session_id, digest)
        await self._buckets.put(
            ctx.org_id, BUCKET, key, sealed, CONTENT_TYPE, deadline=ctx.deadline
        )
        return AgentWorkspaceSnapshot(
            id=snapshot_id, hash=digest, size=len(archive), agent_workspace_id=agent_workspace_id
        )

    def bounded(self, size: int) -> None:
        """Refuses (`SnapshotRefused`) a directory's archive of `size` bytes
        past the bound, before any byte of it is read, and names both."""
        if size > self.directory_bound:
            raise SnapshotRefused(
                f"the workspace's directory packs to {size} bytes, "
                f"past the bound of {self.directory_bound}, so no snapshot is kept"
            )

    async def keep_parts(
        self,
        ctx: TenantContext,
        agent_session_id: UUID,
        snapshot_id: UUID,
        agent_workspace_id: UUID,
        size: int,
        read: Callable[[int], AsyncGenerator[bytes]],
    ) -> AgentWorkspaceSnapshot:
        """A directory's archive of `size` bytes, as `read` gives it in parts
        of `part_bytes`, kept as the session's snapshot under `snapshot_id`:
        each part sealed under the session's key and put under its names,
        then the manifest that names them, kept as any archive is. Refused
        past the bound before any part is read, and when the session keeps
        no content at rest (`SnapshotRefused`). One that fails part way
        removes every part it put, so no step names it and the snapshot
        before stays the latest."""
        self.bounded(size)
        put: list[str] = []
        parts: list[Part] = []
        try:
            async with contextlib.aclosing(read(self.part_bytes)) as stream:
                async for data in stream:
                    if sum(part.size for part in parts) + len(data) > size:
                        raise SnapshotRefused(
                            f"the workspace's directory read past the {size} bytes it packed to"
                        )
                    digest = HASH_SCHEME + await self._keyed_hash(ctx, agent_session_id, data)
                    sealed = await self._seal.seal(ctx, agent_session_id, digest, data)
                    if sealed is None:
                        raise _unkept(agent_session_id)
                    key = part_key(agent_session_id, snapshot_id, len(parts))
                    put.append(key)
                    await self._buckets.put(
                        ctx.org_id, BUCKET, key, sealed, CONTENT_TYPE, deadline=ctx.deadline
                    )
                    parts.append(Part(hash=digest, size=len(data)))
            if sum(part.size for part in parts) != size:
                raise SnapshotRefused(
                    f"the workspace's directory read short of the {size} bytes it packed to"
                )
            manifest = Manifest(snapshot_id=snapshot_id, size=size, parts=tuple(parts))
            kept = await self.keep(
                ctx, agent_session_id, snapshot_id, agent_workspace_id, _manifest(manifest)
            )
        except BaseException:
            await self._remove(ctx, put)
            raise
        return kept.model_copy(update={"size": size, "holds": SnapshotHolds.DIRECTORY})

    async def manifest(
        self, ctx: TenantContext, agent_session_id: UUID, snapshot: AgentWorkspaceSnapshot
    ) -> Manifest:
        """What a directory's snapshot the session keeps names, trusted only
        once it opens under its key and hashes to what its step names, and
        holds the size the step names. Anything else loses the workspace
        that would start from it (`AgentWorkspaceLost`)."""
        what = f"snapshot {snapshot.id} of agent session {agent_session_id}"
        manifest = _opened(await self.load(ctx, agent_session_id, snapshot), what)
        if manifest.size != snapshot.size:
            raise AgentWorkspaceLost(f"{what} does not match its size")
        return manifest

    async def parts(
        self, ctx: TenantContext, agent_session_id: UUID, manifest: Manifest
    ) -> AsyncIterator[bytes]:
        """The parts `manifest` names, in order, each trusted only once it
        opens under the session's key and hashes to what the manifest names
        it by. One gone, erased, or altered loses the workspace being
        restored from it (`AgentWorkspaceLost`), and nothing kept is
        touched."""
        for index, part in enumerate(manifest.parts):
            what = f"part {index} of snapshot {manifest.snapshot_id}"
            key = part_key(agent_session_id, manifest.snapshot_id, index)
            try:
                sealed = await self._buckets.get(ctx.org_id, BUCKET, key, deadline=ctx.deadline)
            except InfraException as error:
                if error.code != BlobNotFound.code:
                    raise
                raise AgentWorkspaceLost(f"{what} is gone from the store") from error
            try:
                data = await self._seal.open(ctx, agent_session_id, part.hash, sealed)
            except ValueError as error:
                raise AgentWorkspaceLost(
                    f"{what} does not open: its stored bytes were altered"
                ) from error
            if data is None:
                raise AgentWorkspaceLost(f"{what} was erased with its session's key")
            digest = HASH_SCHEME + await self._keyed_hash(ctx, agent_session_id, data)
            if digest != part.hash or len(data) != part.size:
                raise AgentWorkspaceLost(f"{what} does not match its hash")
            yield data

    async def load(
        self, ctx: TenantContext, agent_session_id: UUID, snapshot: AgentWorkspaceSnapshot
    ) -> bytes:
        """The bytes of a snapshot the session keeps, trusted only once they
        open under its key and hash to what its step names. Anything else
        loses the workspace that would start from it (`AgentWorkspaceLost`)."""
        what = f"snapshot {snapshot.id} of agent session {agent_session_id}"
        try:
            sealed = await self._buckets.get(
                ctx.org_id,
                BUCKET,
                snapshot_key(agent_session_id, snapshot.hash),
                deadline=ctx.deadline,
            )
        except InfraException as error:
            if error.code != BlobNotFound.code:
                raise
            raise AgentWorkspaceLost(f"{what} is gone from the store") from error
        try:
            archive = await self._seal.open(ctx, agent_session_id, snapshot.hash, sealed)
        except ValueError as error:
            raise AgentWorkspaceLost(
                f"{what} does not open: its stored bytes were altered"
            ) from error
        if archive is None:
            raise AgentWorkspaceLost(f"{what} was erased with its session's key")
        digest = HASH_SCHEME + await self._keyed_hash(ctx, agent_session_id, archive)
        # A directory's snapshot names its archive's size, which its
        # manifest holds.
        whole = snapshot.holds is SnapshotHolds.WORKSPACE
        if digest != snapshot.hash or (whole and len(archive) != snapshot.size):
            raise AgentWorkspaceLost(f"{what} does not match its hash")
        return archive

    async def scan(
        self, ctx: TenantContext, held: AsyncIterable[bytes], also: Collection[str] = ()
    ) -> None:
        """Refuses a snapshot whose bytes, `held` as its provider reads them
        (`AgentWorkspaceProviderInterface.held`), hold the value of a secret the
        catalog's tools may have injected, or one named in `also`, the secrets
        a session's own tools beyond the catalog may have, in any form
        redaction matches, and names the secret, never its value. A secret the
        tenant does not hold was never given, so it is not looked for."""
        values: dict[str, str] = {}
        for name in sorted({*self._secret_names, *also}):
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

    async def _remove(self, ctx: TenantContext, keys: Collection[str]) -> None:
        """The parts of a snapshot that was not kept removed. One that cannot
        be is noise under the session's key, named by no step, and goes with
        the session's purge."""
        for key in keys:
            try:
                await self._buckets.delete(ctx.org_id, BUCKET, key)
            except Exception:
                log.warning("a part of a snapshot that was not kept stays: %s", key, exc_info=True)

    async def purge(self, org_id: UUID, agent_session_id: UUID) -> None:
        """Every snapshot the session keeps, its parts included, a page at a
        time."""
        prefix = session_prefix(agent_session_id)
        while True:
            keys = await self._buckets.list(org_id, BUCKET, prefix, self._purge_batch)
            for key in keys:
                await self._buckets.delete(org_id, BUCKET, key)
            if len(keys) < self._purge_batch:
                return
