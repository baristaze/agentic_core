"""The one way infra reaches the local Docker: its command line, one process a
call, each bounded by a timeout. The container workspace and the container
transport share it. The command line's own environment is the one Docker
needs and nothing of the engine's credentials: the path, the home, and the
`DOCKER_*` variables that pick the daemon."""

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

DOCKER_VARIABLES = (
    "PATH",
    "HOME",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY",
)
"""What the command line reads of this process's environment, and nothing
else."""


@dataclass(frozen=True)
class DockerReply:
    code: int | None  # None when it did not answer in time
    stdout: bytes
    stderr: bytes

    @property
    def ok(self) -> bool:
        return self.code == 0

    def reason(self) -> str:
        if self.code is None:
            return "docker did not answer in time"
        lines = self.stderr.decode(errors="replace").strip().splitlines()
        return lines[-1] if lines else f"docker exited {self.code}"


def docker_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The command line's environment: what picks the daemon, and `extra`,
    which a `docker exec -e NAME` passes on by name, so no value is ever on a
    command line another process can list."""
    base = {name: os.environ[name] for name in DOCKER_VARIABLES if name in os.environ}
    return {**base, **(extra or {})}


async def docker(
    *args: str,
    bound: timedelta,
    stdin: bytes | None = None,
    env: Mapping[str, str] | None = None,
) -> DockerReply:
    """Runs `docker <args>` and answers its exit and output; a command that
    has not ended within `bound` is killed and answers no code. A command line
    that is not installed answers 127, as a shell would."""
    try:
        process = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=docker_environment(env),
        )
    except FileNotFoundError:
        return DockerReply(127, b"", b"docker is not installed")
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(stdin), bound.total_seconds())
    except TimeoutError:
        process.kill()
        await process.wait()
        return DockerReply(None, b"", b"")
    return DockerReply(process.returncode, stdout, stderr)
