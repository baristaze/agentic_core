"""The one settings object of the session runner.

The runner reads the same `.env` the other processes do, so its own knobs
carry a prefix of their own, `ACME_RUNNER_`: the maintenance worker's
`ACME_WORKER_*` and its metrics port never reach it, and two processes on
one host never bind one port."""

import os
import socket

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from acme.infra.impl.settings import InfraSettings
from acme.integrations.settings import IntegrationsSettings
from acme.om.storage.settings import StorageSettings
from acme.om.work.types.work_item import WorkKind, relayed_lane


def default_runner_id() -> str:
    return f"session-runner-{socket.gethostname()}-{os.getpid()}"


class SessionRunnerSettings(StorageSettings, InfraSettings, IntegrationsSettings):
    model_config = SettingsConfigDict(env_prefix="ACME_", env_file=".env", extra="ignore")

    service_name: str = "session-runner"
    version: str = "0.1.0"
    # The runner serves no HTTP but its metrics and its probe; containers
    # bind 0.0.0.0.
    metrics_host: str = "127.0.0.1"
    runner_metrics_port: int = 9465
    runner_id: str = Field(default_factory=default_runner_id)
    # The loops' own lane, where the relay lands them (`WORK_LANES`), so a
    # cap on the maintenance worker's lane counts none of a tenant's loops.
    runner_lane: str = relayed_lane(WorkKind.LOOP)
    # Each is a count or a duration the claim loop divides or waits on, so
    # zero is a broken setting, refused at start, as the maintenance
    # worker's are. A loop runs for minutes, so a runner holds few at once;
    # its lease is renewed while the loop runs, and a runner that dies
    # leaves its loop to the next claim once the lease runs out.
    runner_capacity: int = Field(default=4, gt=0)
    runner_lease_seconds: int = Field(default=60, gt=0)
    runner_heartbeat_seconds: int = Field(default=10, gt=0)
    runner_sweep_seconds: int = Field(default=30, gt=0)
    runner_poll_seconds: int = Field(default=5, gt=0)
