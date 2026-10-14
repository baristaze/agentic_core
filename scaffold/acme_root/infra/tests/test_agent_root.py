"""The engine's infra, as each infra root builds it with one call: the local
root's twins work, a deployed environment refuses the engine's local-only
backends beside the guideline's, the key service's root comes from
settings, and a workspace backend builds its provider and its transport
together."""

from pathlib import Path

import pytest

from acme.infra.agent_root import AGENT_UNSAFE_IN_CLOUD
from acme.infra.agent_workspaces import EgressMode, EgressPolicy, IsolationMode, IsolationSpec
from acme.infra.base import new_id
from acme.infra.impl.configured import InfraConfiguredImpl, UnsafeConfiguration
from acme.infra.impl.local import InfraLocalImpl
from acme.infra.impl.settings import InfraSettings

CLOUD_BACKENDS = {
    "secrets_backend": "aws",
    "cache_backend": "valkey",
    "topics_backend": "valkey",
    "buckets_backend": "s3",
    "queues_backend": "sqs",
    "keys_backend": "kms",
    "flags_backend": "none",
}


@pytest.fixture(autouse=True)
def _away_from_the_checkout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The settings read `.env` from the working directory; a test builds
    its settings from its own values alone."""
    monkeypatch.chdir(tmp_path)


def local_settings(tmp_path: Path, **overrides: object) -> InfraSettings:
    base = {
        "environment": "local",
        "buckets_root": tmp_path / "buckets",
        "secrets_file": tmp_path / "secrets.env",
        "flags_file": tmp_path / "flags.json",
    }
    return InfraSettings.model_validate({**base, **overrides})


async def test_the_engines_twins_work_over_the_local_root(tmp_path: Path) -> None:
    infra = InfraLocalImpl(tmp_path)
    await infra.start()
    org = new_id()
    spec = IsolationSpec(mode=IsolationMode.TWIN, egress=EgressPolicy(mode=EgressMode.NONE))
    workspace = await infra.get_agent_workspaces().prepare(org, new_id(), spec)
    await infra.get_transport().write_file(workspace, "a.txt", b"hi", epoch=1)
    assert await infra.get_transport().read_file(workspace, "a.txt", 10) == b"hi"
    org_key = await infra.get_keys().generate(org, new_id(), 1)
    assert len(org_key.plaintext) == 32
    assert infra.describe()[-6:-1] == [
        "keys=memory(random root)",
        "vms=twin",
        "workspaces=twin",
        "transport=twin",
        "broker=twin",
    ]
    await infra.close()


@pytest.mark.parametrize("environment", ["dev", "staging", "production"])
@pytest.mark.parametrize(("field", "value", "env_name"), AGENT_UNSAFE_IN_CLOUD)
def test_deployed_environments_refuse_the_engines_local_backends(
    tmp_path: Path, environment: str, field: str, value: str, env_name: str
) -> None:
    with pytest.raises(UnsafeConfiguration) as raised:
        InfraConfiguredImpl(
            local_settings(tmp_path, environment=environment, **{**CLOUD_BACKENDS, field: value})
        )
    assert env_name in raised.value.message


def test_the_keys_root_comes_from_settings_and_refuses_another_shape(tmp_path: Path) -> None:
    rooted = InfraConfiguredImpl(
        local_settings(tmp_path, keys_root_key="bG9jYWwtb25seS1rZXlzLXJvb3Qtbm90LXNlY3JldCE=")
    )
    assert rooted.get_keys().describe() == "keys=memory(root from settings)"
    with pytest.raises(ValueError, match="32 bytes"):
        InfraConfiguredImpl(local_settings(tmp_path, keys_root_key="c2hvcnQ="))


@pytest.mark.parametrize(
    ("backend", "lines"),
    [
        ("host", ["workspaces=host({root})", "transport=local"]),
        (
            "account",
            ["workspaces=account(acme-agent, {root})", "transport=local(acme-agent)"],
        ),
        ("container", ["workspaces=container(python:3.14-slim)", "transport=container"]),
        ("vm", ["workspaces=vm(template:_images/ubuntu-lts)", "transport=vm"]),
    ],
)
def test_a_workspace_backend_builds_its_provider_and_its_transport_together(
    tmp_path: Path, backend: str, lines: list[str]
) -> None:
    """The provider and the transport that runs in what it prepares are one
    choice, and building them reaches nothing."""
    root = tmp_path / "workspaces"
    infra = InfraConfiguredImpl(
        local_settings(tmp_path, agent_workspace_backend=backend, agent_workspaces_root=root)
    )
    runtime = [line for line in infra.describe() if line.startswith(("workspaces=", "transport="))]
    assert runtime == [line.format(root=root) for line in lines]
