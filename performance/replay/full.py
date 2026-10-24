"""The whole run on the performance stack, and one point of it.

Before each point, the stack gets what a deployment's would hold: the
wrapping key and its alias in the KMS emulator, and the tool outputs'
bucket in MinIO. Each point builds the engine afresh over the stack, with
a tenant of its own, so no point warms another's caches.

While each point writes, once half its turns are played, a scan reads the
rows of its sessions' steps and every tool output object they kept, and
counts the run's marker in them, as it is and in base64; another scan does
the same once the point is done. Each scan also counts the rows and the
objects it read, so a scan that read nothing shows. The scans run on a
thread of their own, so the profile of the event loop's thread holds none
of their work."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import platform
import statistics
import subprocess
import threading
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, cast
from uuid import UUID, uuid4

import aioboto3
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import MigrationSettings
from acme.workers.agent_session_runner.settings import AgentSessionRunnerSettings
from replay.engine import MAIN_MODEL, Engine
from replay.generate import runs
from replay.run import point
from replay.shape import Turn
from replay.words import Words, marker

SEED = 1
BUCKET = "tool-outputs"
STATS_FLUSH = 1.5
"""Seconds for every backend to report its counts to the server's
statistics, which it does at most once a second."""
T = TypeVar("T")


def encodings(mark: str) -> list[bytes]:
    """The marker as it is, and as base64 of any alignment, in either
    alphabet: what a row or an object holding it in the clear would show."""
    raw = mark.encode()
    found = [raw]
    for pad in range(3):
        encoded = base64.b64encode(b"\0" * pad + raw)
        piece = encoded[-(-8 * pad // 6) : (8 * (pad + len(raw))) // 6]
        found += [piece, piece.replace(b"+", b"-").replace(b"/", b"_")]
    return found


@dataclass
class Scan:
    rows: int = 0
    rows_read_as_text: int = 0
    rows_with_marker: int = 0
    objects: int = 0
    objects_with_marker: int = 0
    content_bytes: int = 0
    content_stored_bytes: int = 0
    object_bytes: int = 0
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {k: round(v, 3) if isinstance(v, float) else v for k, v in vars(self).items()}


def _aws(settings: AgentSessionRunnerSettings) -> aioboto3.Session:
    return aioboto3.Session(
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.aws_region,
    )


async def scan(settings: AgentSessionRunnerSettings, org_id: UUID, sessions: list[UUID], mark: str) -> Scan:
    """The marker counted in the steps' rows of `sessions`, read as a
    database reader reads them, and in every tool output object they kept."""
    started = time.perf_counter()
    found = Scan()
    pieces = encodings(mark)
    patterns = [f"%{piece.decode()}%" for piece in pieces]
    engine = create_async_engine(reader()[DatabaseRole.ACTIVITY])
    ids = [str(s) for s in sessions]
    try:
        async with engine.connect() as connection:
            found.rows = int(
                await connection.scalar(
                    text("SELECT count(*) FROM activity.agent_steps WHERE agent_session_id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": ids},
                )
                or 0
            )
            # The control: each row's text holds its own session's id, so the
            # same match over the same text finds every row.
            found.rows_read_as_text = int(
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM activity.agent_steps s WHERE agent_session_id = ANY(CAST(:ids AS uuid[])) "
                        "AND s::text LIKE '%' || agent_session_id::text || '%'"
                    ),
                    {"ids": ids},
                )
                or 0
            )
            sized = (
                await connection.execute(
                    text(
                        "SELECT coalesce(sum(octet_length(content::text)), 0), coalesce(sum(pg_column_size(content)), 0) "
                        "FROM activity.agent_steps WHERE agent_session_id = ANY(CAST(:ids AS uuid[])) "
                        "AND content->>'state' = 'sealed'"
                    ),
                    {"ids": ids},
                )
            ).one()
            found.content_bytes, found.content_stored_bytes = int(sized[0]), int(sized[1])
            found.rows_with_marker = int(
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM activity.agent_steps s WHERE agent_session_id = ANY(CAST(:ids AS uuid[])) "
                        "AND s::text LIKE ANY(CAST(:patterns AS text[]))"
                    ),
                    {"ids": ids, "patterns": patterns},
                )
                or 0
            )
    finally:
        await engine.dispose()
    async with _aws(settings).client("s3", endpoint_url=settings.s3_endpoint_url) as s3:
        bucket = f"{settings.s3_bucket_prefix}-{BUCKET}"
        for agent_session_id in sessions:
            listed = await s3.list_objects_v2(Bucket=bucket, Prefix=f"{org_id}/agent-sessions/{agent_session_id}/")
            for item in listed.get("Contents", []):
                body = await (await s3.get_object(Bucket=bucket, Key=item["Key"]))["Body"].read()
                found.objects += 1
                found.object_bytes += len(body)
                found.objects_with_marker += any(piece in body for piece in pieces)
    found.seconds = time.perf_counter() - started
    return found


def reader() -> dict[DatabaseRole, str]:
    """Each role's database as its server's superuser, the stack's own login,
    which every row-level policy lets through: a reader of the database that
    sees every row there is."""
    migration = MigrationSettings(_env_file=None)
    superuser = make_url(migration.master_url()).set(username="postgres", password="postgres")
    return migration.under_login(superuser.render_as_string(hide_password=False))


async def transactions() -> dict[str, int]:
    """Each role's database's transactions so far, as the server counts
    them."""
    counted: dict[str, int] = {}
    for role, url in reader().items():
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                counted[role.value] = int(
                    await connection.scalar(
                        text("SELECT xact_commit + xact_rollback FROM pg_stat_database WHERE datname = current_database()")
                    )
                    or 0
                )
        finally:
            await engine.dispose()
    return counted


def apart(work: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """`work` on a thread and an event loop of its own, so the sampler, which
    reads the main thread alone, holds none of it."""
    box: list[Any] = []

    def go() -> None:
        try:
            box.append(asyncio.run(work()))
        except BaseException as failed:
            box.append(failed)

    thread = threading.Thread(target=go, name="performance-apart")
    thread.start()
    thread.join()
    (result,) = box
    if isinstance(result, BaseException):
        raise result
    return cast(T, result)


async def prepare(settings: AgentSessionRunnerSettings) -> None:
    """The wrapping key and its alias in the emulator, and the bucket in
    MinIO, unless they are there. It refuses a KMS endpoint that is not a
    local one, so no call of the run reaches a real KMS."""
    endpoint = os.environ.get("AWS_ENDPOINT_URL_KMS", "")
    if not endpoint.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise RuntimeError("AWS_ENDPOINT_URL_KMS names no local emulator")
    aws = _aws(settings)
    async with aws.client("kms", region_name=settings.aws_region) as kms:
        if kms.meta.endpoint_url != endpoint:
            raise RuntimeError("the KMS client does not reach the emulator")
        aliases = {alias["AliasName"] for alias in (await kms.list_aliases()).get("Aliases", [])}
        if settings.kms_key_id not in aliases:
            made = await kms.create_key(Description="the performance run's wrapping key")
            await kms.create_alias(AliasName=settings.kms_key_id, TargetKeyId=made["KeyMetadata"]["KeyId"])
    async with aws.client("s3", endpoint_url=settings.s3_endpoint_url) as s3:
        bucket = f"{settings.s3_bucket_prefix}-{BUCKET}"
        if bucket not in {b["Name"] for b in (await s3.list_buckets()).get("Buckets", [])}:
            await s3.create_bucket(Bucket=bucket)


async def calibrate(engine: Engine) -> dict[str, float]:
    """What one round trip costs on this stack, so a reader can scale the
    run to another: a query that does nothing, and one unwrap of a data key
    by the key service the engine was built with."""
    rounds = 200
    database = create_async_engine(reader()[DatabaseRole.ACTIVITY])
    took: list[float] = []
    try:
        async with database.connect() as connection:
            for _ in range(rounds):
                started = time.perf_counter()
                await connection.execute(text("SELECT 1"))
                took.append(time.perf_counter() - started)
    finally:
        await database.dispose()
    keys: Any = engine.infra.get_keys()
    service = getattr(keys, "_inner", keys)
    org_id, key_id = engine.ctx().org_id, uuid4()
    made = await service.generate(org_id, key_id, 1)
    unwraps: list[float] = []
    for _ in range(rounds):
        started = time.perf_counter()
        await service.unwrap(org_id, key_id, 1, made.wrapped)
        unwraps.append(time.perf_counter() - started)
    return {
        "database_round_trip_ms": round(statistics.median(took) * 1000, 3),
        "key_unwrap_ms": round(statistics.median(unwraps) * 1000, 3),
    }


def reaches_emulator(engine: Engine) -> bool:
    """The engine's own KMS client, as its adapter opened it, names the
    emulator."""
    holder = getattr(getattr(engine.infra.get_keys(), "_inner", None), "_holder", None)
    client = holder.client() if holder is not None else None
    return client is not None and client.meta.endpoint_url == os.environ.get("AWS_ENDPOINT_URL_KMS")


async def one_point(shape: tuple[Turn, ...], keys: str, sessions: int, turns: int, *, profiled: bool) -> dict[str, Any]:
    settings = AgentSessionRunnerSettings(_env_file=None, keys_backend=keys)
    await prepare(settings)
    mark = marker()
    words = Words(SEED, mark)
    engine = Engine.on_postgres(keys, words)
    await engine.start()
    try:
        if keys == "kms" and not reaches_emulator(engine):
            raise RuntimeError("the engine's KMS adapter does not reach the emulator")
        calibration = await calibrate(engine)
        scripts = [runs(shape, turns, words, MAIN_MODEL) for _ in range(sessions)]
        org_id = engine.ctx().org_id
        halfway: asyncio.Future[list[UUID]] = asyncio.get_running_loop().create_future()

        def answered(count: int) -> None:
            if count >= sessions * turns // 2 and not halfway.done():
                halfway.set_result(list(engine.twin.scripts))

        engine.twin.on_answer = answered
        during: list[Scan] = []

        async def scan_halfway() -> None:
            ids = await halfway
            during.append(await asyncio.to_thread(apart, lambda: scan(settings, org_id, ids, mark)))

        watcher = asyncio.create_task(scan_halfway())
        before = await asyncio.to_thread(apart, transactions)
        measured, ids = await point(engine, scripts, profiled=profiled)
        await watcher
        await asyncio.sleep(STATS_FLUSH)
        counted = await asyncio.to_thread(apart, transactions)
        after = await asyncio.to_thread(apart, lambda: scan(settings, org_id, ids, mark))
        made = {role: counted[role] - before[role] for role in counted}
        return {
            "keys": keys,
            **measured.as_dict(),
            "calibration": calibration,
            "transactions": made,
            "transactions_per_step_written": round(sum(made.values()) / max(1, measured.steps_written), 2),
            "scan_while_writing": during[0].as_dict(),
            "scan_after": after.as_dict(),
        }
    finally:
        await engine.close()


def machine() -> dict[str, Any]:
    def sysctl(name: str) -> str:
        try:
            return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    return {
        "platform": platform.platform(terse=True),
        "processor": sysctl("machdep.cpu.brand_string") or platform.processor(),
        "cores": os.cpu_count(),
        "memory_gb": round(int(sysctl("hw.memsize") or 0) / 2**30),
        "python": platform.python_version(),
    }


async def full(shape: tuple[Turn, ...], name: str, results: Path, turns: int, sweep: tuple[int, ...], sweep_turns: int) -> int:
    report: dict[str, Any] = {
        "shape": name,
        "shape_turns": len(shape),
        "turns": turns,
        "sweep_turns": sweep_turns,
        "machine": machine(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runs": [],
    }
    results.parent.mkdir(parents=True, exist_ok=True)
    for keys in ("kms", "memory"):
        print(f"{keys}: one session of {turns} turns", flush=True)
        report["runs"].append({"name": "alone", **await one_point(shape, keys, 1, turns, profiled=True)})
        for sessions in sweep:
            print(f"{keys}: {sessions} sessions of {sweep_turns} turns", flush=True)
            report["runs"].append(
                {"name": f"sweep-{sessions}", **await one_point(shape, keys, sessions, sweep_turns, profiled=True)}
            )
            results.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    # The sampler's own cost: the sweep's first point again, with no sampler.
    print(f"kms: 1 session of {sweep_turns} turns, unprofiled", flush=True)
    report["runs"].append({"name": "sweep-1-unprofiled", **await one_point(shape, "kms", 1, sweep_turns, profiled=False)})
    report["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    results.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    unread = [
        f"{run['keys']} {run['name']}"
        for run in report["runs"]
        for found in (run["scan_while_writing"], run["scan_after"])
        if found["rows_with_marker"]
        or found["objects_with_marker"]
        or not found["rows"]
        or found["rows_read_as_text"] != found["rows"]
        or not found["objects"]
    ]
    print(f"wrote {results}; scans that found a marker, or read no row or no object: {unread or 'none'}")
    return 1 if unread else 0
