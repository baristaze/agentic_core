"""Workspace bases as the tools build and keep them: an image and its setup,
built once for a tenant and kept as a snapshot every workspace on it starts
from (ADR 1028).

A base is the tenant's, not a session's. It holds what its setup wrote and
no session's content, so it is not sealed under a session's key: it sits in
the snapshots bucket under the tenant's own prefix, where no other tenant
reads it, and goes with the tenant's purge. Its key is a hash of the mode
its workspaces run in, its image, its commands, and the setup's egress, so
a changed base is another key and is built again.

A setup runs in a workspace of its own, with the setup's egress alone: no
secret is given to it, no session's content is in it, and what it printed
is not kept. A build is claimed first, so one prepare builds while the
others wait on it; one that fails keeps nothing, and the next prepare
builds again."""

import hashlib
import json
from collections.abc import AsyncIterable, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from acme.infra.buckets import BlobNotFound, Buckets, BucketsInterface
from acme.infra.cache import CacheInterface
from acme.infra.exceptions import InfraException
from acme.infra.transports import CommandSpec, RecordSeal, TransportInterface
from acme.infra.workspaces import (
    BaseSetupFailed,
    Durability,
    IsolationRefused,
    IsolationSpec,
    SnapshotRefused,
    WorkspaceBase,
    WorkspaceProviderInterface,
)
from acme.om.base import derived_id, new_id
from acme.om.context import TenantContext

BUCKET = Buckets.SNAPSHOTS
CONTENT_TYPE = "application/octet-stream"
PREFIX = "workspace-bases/"
"""Where a tenant's bases sit in the bucket, apart from its sessions'
snapshots."""

Scan = Callable[[TenantContext, AsyncIterable[bytes]], Awaitable[None]]
"""Refuses a snapshot whose bytes, as its provider reads them, hold the
value of a secret a tool may be given (`SnapshotRefused`)."""


def base_key(spec: IsolationSpec) -> str:
    """The key of the base `spec` names: a hash of the mode its workspaces
    run in and of the base whole, its image, its commands, and its egress."""
    named = {"mode": spec.mode.value, "base": None}
    if spec.base is not None:
        named["base"] = spec.base.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(named, sort_keys=True).encode()).hexdigest()
    return PREFIX + digest


KEPT_AT = datetime(1970, 1, 1, tzinfo=UTC)
"""A base's workspace holds no time of its own: its id is its tenant's and
its key's alone."""


def base_workspace_id(org_id: UUID, key: str) -> UUID:
    """The workspace a provider keeps a base's snapshot under, when it keeps
    one outside the archive, such as a disk: the tenant's and the key's
    alone, so every build, the base's drop, and the tenant's purge find the
    same one."""
    return derived_id(org_id, KEPT_AT, key)


async def _unkept(data: bytes) -> None:
    return None


UNKEPT = RecordSeal(seal=_unkept, open=_unkept)
"""A setup's output is not kept: its record says how it ended alone."""


class WorkspaceBases:
    """`build_limit` bounds a build whole, its setup's commands together,
    and is how long its claim holds."""

    def __init__(
        self,
        buckets: BucketsInterface,
        workspaces: WorkspaceProviderInterface,
        transport: TransportInterface,
        claims: CacheInterface,
        scan: Scan,
        build_limit: timedelta,
        purge_batch: int,
        clock: Callable[[], datetime],
    ) -> None:
        self._buckets = buckets
        self._workspaces = workspaces
        self._transport = transport
        self._claims = claims
        self._scan = scan
        self._build_limit = build_limit
        self._purge_batch = purge_batch
        self._clock = clock

    async def archive(self, ctx: TenantContext, spec: IsolationSpec) -> bytes:
        """The snapshot of the base `spec` names, read when the tenant keeps
        it and built when it does not. A build another prepare holds the
        claim on refuses this one, and the refusal clears once it is kept.
        `BaseSetupFailed` when a setup command fails, with nothing kept."""
        key = base_key(spec)
        kept = await self._read(ctx, key)
        if kept is not None:
            return kept
        # The cache fails open: with no count, this prepare builds, and a
        # second build of the same base keeps the same base again.
        count, _ = await self._claims.increment(ctx.org_id, key, self._build_limit)
        if count > 1:
            raise IsolationRefused(f"the workspace base {key} is being built", clears=True)
        try:
            # One kept between the read and the claim is the one it takes.
            kept = await self._read(ctx, key)
            if kept is None:
                kept = await self._build(ctx, spec)
                await self._buckets.put(
                    ctx.org_id, BUCKET, key, kept, CONTENT_TYPE, deadline=ctx.deadline
                )
        finally:
            await self._claims.invalidate(ctx.org_id, key)
        return kept

    async def drop(self, ctx: TenantContext, spec: IsolationSpec) -> None:
        """The base `spec` names removed, what its provider keeps of it
        first, so the next prepare builds it again."""
        key = base_key(spec)
        await self._workspaces.purge(ctx.org_id, base_workspace_id(ctx.org_id, key))
        await self._buckets.delete(ctx.org_id, BUCKET, key)

    async def purge(self, org_id: UUID) -> int:
        """A page of the tenant's bases removed, what their provider keeps of
        each first; how many went."""
        keys = await self._buckets.list(org_id, BUCKET, PREFIX, self._purge_batch)
        for key in keys:
            await self._workspaces.purge(org_id, base_workspace_id(org_id, key))
            await self._buckets.delete(org_id, BUCKET, key)
        return len(keys)

    async def _build(self, ctx: TenantContext, spec: IsolationSpec) -> bytes:
        """The base's setup run in a workspace of its own, on its image with
        the setup's egress, the spec's limits, and what a package manager
        needs (`building`), then snapshotted and scanned. The workspace and the records of its commands go, however
        the build ends."""
        base = spec.base
        if base is None:
            raise ValueError("a spec with no base has no base to build")
        setup_spec = spec.model_copy(
            update={
                "egress": base.egress,
                "durability": Durability.SNAPSHOT,
                "base": WorkspaceBase(image=base.image),
            }
        )
        build_id = new_id()
        kept_under = base_workspace_id(ctx.org_id, base_key(spec))
        deadline = self._clock() + self._build_limit
        try:
            workspace = await self._workspaces.prepare(
                ctx.org_id, build_id, setup_spec, building=True
            )
            for index, command in enumerate(base.setup):
                ran = await self._transport.run(
                    workspace,
                    CommandSpec(
                        argv=("sh", "-c", command), key=new_id(), epoch=0, deadline=deadline
                    ),
                    seal=UNKEPT,
                )
                if ran.exit_code != 0:
                    raise BaseSetupFailed(index, command, ran.exit_code)
            taken = await self._workspaces.snapshot(workspace)
            # Kept under the base's own workspace, so the build's purge
            # leaves what the provider keeps outside the archive.
            archive = await self._workspaces.keep(taken, ctx.org_id, kept_under)
        finally:
            await self._workspaces.purge(ctx.org_id, build_id)
            await self._transport.purge_records(build_id)
        try:
            await self._scan(ctx, self._workspaces.held(archive))
        except InfraException as refused:
            await self._workspaces.purge(ctx.org_id, kept_under)
            if refused.code != SnapshotRefused.code:
                raise
            raise IsolationRefused(
                f"the workspace base is not kept: {refused.message}"
            ) from refused
        return archive

    async def _read(self, ctx: TenantContext, key: str) -> bytes | None:
        try:
            return await self._buckets.get(ctx.org_id, BUCKET, key, deadline=ctx.deadline)
        except InfraException as error:
            if error.code != BlobNotFound.code:
                raise
            return None
