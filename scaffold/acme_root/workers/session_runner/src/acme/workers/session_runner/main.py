"""The runner binary: settings, container, stop handlers, `serve`, and the
`health` probe, which asks the serving process's `/healthz`.

The runner claims one kind of work, `LOOP`, with the maintenance worker's
claim loop: a lease it renews while the loop runs, a fence that cancels a
run whose lease is lost, a liveness beat, and a drain on stop. Its sweep
takes back the expired leases and relays the outbox a crash left, and
purges nothing."""

import argparse
import asyncio
import logging
import signal
import sys
import urllib.error
import urllib.request
from datetime import timedelta

from acme.infra.cache import CacheScope
from acme.infra.observability import (
    configure_error_reporting,
    configure_logging,
    configure_tracing,
    name_process,
)
from acme.infra.trust import install_trust_store
from acme.om.agents.types.kind import AgentKind
from acme.om.tools.tool import ToolInterface
from acme.om.work.types.work_item import WorkKind
from acme.workers.maintenance.health import Probe, WorkerHttpServer
from acme.workers.maintenance.loop import LoopOptions, WorkerLoop
from acme.workers.session_runner.container import RunnerContainer
from acme.workers.session_runner.runs import LoopHandlerImpl
from acme.workers.session_runner.settings import SessionRunnerSettings

log = logging.getLogger(__name__)


def loop_options(settings: SessionRunnerSettings, lane: str | None = None) -> LoopOptions:
    return LoopOptions(
        worker_id=settings.runner_id,
        lane=lane or settings.runner_lane,
        capacity=settings.runner_capacity,
        lease=timedelta(seconds=settings.runner_lease_seconds),
        heartbeat_interval=timedelta(seconds=settings.runner_heartbeat_seconds),
        sweep_interval=timedelta(seconds=settings.runner_sweep_seconds),
        poll_interval=timedelta(seconds=settings.runner_poll_seconds),
        recovery_only=True,
    )


def build_runner(container: RunnerContainer, lane: str | None = None) -> WorkerLoop:
    """The claim loop over one handler, `LOOP`'s, which calls the loop's one
    operation. It purges nothing, so it has no purge of its own."""
    managers = container.managers
    return WorkerLoop(
        work=managers.work,
        outbox=managers.outbox,
        purges={},
        handlers={WorkKind.LOOP: LoopHandlerImpl(managers.loop)},
        topics=container.infra.get_topics(),
        liveness=container.infra.get_cache(CacheScope.WORKER_LIVENESS),
        options=loop_options(container.settings, lane),
    )


def boot(settings: SessionRunnerSettings) -> None:
    """Logging first, then the process's name, the trust store, error
    reporting, and tracing: the order every process boots in."""
    configure_logging(settings.log_level, settings.log_json)
    name_process(settings.service_name, settings.environment)
    install_trust_store()
    configure_error_reporting(
        settings.sentry_dsn, settings.environment, settings.service_name, settings.version
    )
    configure_tracing(
        settings.otel_endpoint,
        settings.service_name,
        timedelta(seconds=settings.otel_timeout_seconds),
    )


async def serve(
    lane: str | None,
    *,
    agent_kinds: tuple[AgentKind, ...] = (),
    tool_catalog: tuple[ToolInterface, ...] = (),
    domain_classes: tuple[str, ...] = (),
) -> int:
    settings = SessionRunnerSettings()
    boot(settings)
    container = RunnerContainer.build(
        settings,
        agent_kinds=agent_kinds,
        tool_catalog=tool_catalog,
        domain_classes=domain_classes,
    )
    await container.start()
    runner = build_runner(container, lane)
    running = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        running.add_signal_handler(sig, runner.stop)
    # /metrics for the collector and /healthz for the container probe: the
    # loop's own last beat, held in memory.
    http = WorkerHttpServer(
        settings.metrics_host, settings.runner_metrics_port, liveness_probe(runner), running
    )
    http.start()
    try:
        await runner.run()
    finally:
        http.stop()
        await container.close()
    log.info("%s stopped", settings.runner_id)
    return 0


def liveness_probe(runner: WorkerLoop) -> Probe:
    async def alive() -> bool:
        return runner.alive()

    return alive


def health(settings: SessionRunnerSettings) -> int:
    """The probe by hand: asks the serving process's `/healthz` and exits 0
    on 200, 1 otherwise."""
    host = "127.0.0.1" if settings.metrics_host == "0.0.0.0" else settings.metrics_host
    url = f"http://{host}:{settings.runner_metrics_port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return 0 if response.status == 200 else 1
    except urllib.error.HTTPError as error:
        print(f"{url} answered {error.code}", file=sys.stderr)
    except OSError as error:
        print(f"{url} is unreachable: {error}", file=sys.stderr)
    return 1


def main(
    argv: list[str] | None = None,
    *,
    agent_kinds: tuple[AgentKind, ...] = (),
    tool_catalog: tuple[ToolInterface, ...] = (),
    domain_classes: tuple[str, ...] = (),
) -> int:
    """`agent_kinds`, `tool_catalog`, and `domain_classes` are the product's:
    a product's own entry point passes them, as its containers pass them to
    the managers."""
    parser = argparse.ArgumentParser(prog="acme-session-runner")
    sub = parser.add_subparsers(dest="command", required=True)
    p_serve = sub.add_parser("serve", help="claim the loops of agent sessions and run them")
    p_serve.add_argument("--lane", help="the lane to claim from; defaults to ACME_RUNNER_LANE")
    sub.add_parser("health", help="exit 0 while the serving runner answers /healthz with 200")
    args = parser.parse_args(argv)
    if args.command == "health":
        return health(SessionRunnerSettings())
    return asyncio.run(
        serve(
            args.lane,
            agent_kinds=agent_kinds,
            tool_catalog=tool_catalog,
            domain_classes=domain_classes,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
