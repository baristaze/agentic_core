"""The tools manager end to end over Postgres, as a process wires it: the
configured infra root with the host workspace backend, so a call runs as a
real process in a directory of its own, and every manager over the
relational root. The tenant's layer is stored, a person's approval lands in
the history, the call's secret is audited by name in the event stream and
redacted from its response, and an unsafe call a lost run left open is
answered from the transport's record without running again, a record that
keeps what the command printed sealed under the session's key."""

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest
from contracts.loops import live
from contracts.tools import (
    HOST_SPEC,
    INJECTED_TOKEN,
    KIND_DEFAULTS,
    Command,
    failure_of,
    put_call,
    registry_of,
    result_text,
)

from acme.infra.impl.configured import InfraConfiguredImpl
from acme.infra.impl.settings import InfraSettings
from acme.infra.transports.redaction import forms, marker
from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.base import new_id, utcnow
from acme.om.context import AppContext, AppType, RequestContext, TenantContext
from acme.om.root import Managers, build_managers
from acme.om.storage.impl.postgres import StoragePostgresImpl
from acme.om.storage.settings import MigrationSettings
from acme.om.tools.impl.manager import SECRET_USED
from acme.om.tools.types.call import GateOutcome
from acme.om.tools.types.policy import Decision, PolicyRule
from acme.om.tools.types.tool import Effect

pytestmark = pytest.mark.integration

APP = AppContext(type=AppType.PORTAL, version="portal@test")
SECRET = "ghs_e2e-4b3a2918-7c6d-token"


@pytest.fixture
async def storage(
    migration_settings: MigrationSettings, migrated: object
) -> AsyncIterator[StoragePostgresImpl]:
    root = StoragePostgresImpl(
        migration_settings.role_urls(),
        migration_settings.role_pools(),
        system_urls=migration_settings.system_role_urls(),
    )
    yield root
    await root.close()


async def an_org(storage: StoragePostgresImpl, tmp_path: Path) -> tuple[TenantContext, Managers]:
    bootstrap = build_managers(storage, InfraConfiguredImpl(settings(tmp_path, {})))
    slug = f"ajax-{new_id().hex[-8:]}"
    owner, _ = await bootstrap.tenancy.bootstrap(
        RequestContext(request_id=new_id(), app=APP),
        "Ajax",
        slug,
        f"ann-{slug}@example.test",
        "Ann",
    )
    overrides = {f"{owner.org_id.hex}_api_token".upper(): SECRET}
    managers = build_managers(
        storage, InfraConfiguredImpl(settings(tmp_path, overrides)), principal_context=live
    )
    return owner, managers


async def a_session(managers: Managers, owner: TenantContext) -> UUID:
    """A session the owner made, whose calls run under the owner: the gate
    asks attribution whose authority each call runs under."""
    session_id, now = new_id(), utcnow()
    session = AgentSession(
        id=session_id,
        created_at=now,
        updated_at=now,
        created_by=owner.user_id,
        updated_by=owner.user_id,
        title="a call over Postgres",
        kind="operator",
        kind_version=1,
        root_id=session_id,
    )
    await managers.agent_sessions.create_session(owner, session)
    await managers.attribution.open_authority(owner, session_id, AuthorityMode.STEADY)
    return session_id


def settings(tmp_path: Path, overrides: dict[str, str]) -> InfraSettings:
    return InfraSettings.model_validate(
        {
            "environment": "local",
            "buckets_root": tmp_path / "buckets",
            "secrets_file": tmp_path / "secrets.env",
            "secret_overrides": overrides,
            "workspace_backend": "host",
            "workspaces_root": tmp_path / "workspaces",
        }
    )


async def test_a_call_is_approved_run_audited_and_recovered_over_postgres(
    storage: StoragePostgresImpl, tmp_path: Path
) -> None:
    owner, managers = await an_org(storage, tmp_path)
    tools, steps = managers.tools, managers.steps
    python = str(Path(sys.executable))
    registry = registry_of(
        Command("call_api", secrets=(INJECTED_TOKEN,)), Command("deploy", effect=Effect.UNSAFE)
    )
    # The tenant narrows the kind's default: this one call waits for a person.
    policy = await tools.get_policy(owner)
    narrowed = (PolicyRule(tool="call_api", decision=Decision.APPROVE),)
    stored = await tools.write_policy(owner, policy.model_copy(update={"rules": narrowed}))
    assert (await tools.get_policy(owner)) == stored

    session = await a_session(managers, owner)
    workspace = await tools.prepare_workspace(owner, session, HOST_SPEC)
    script = "import os; print('token', os.environ['API_TOKEN'])"
    found = await put_call(
        tools,
        steps,
        owner,
        "call_api",
        {"argv": [python, "-c", script]},
        "execute",
        session_id=session,
    )
    gate = await tools.gate(
        owner, registry, KIND_DEFAULTS, found.request, found.call_input, workspace
    )
    assert gate.outcome is GateOutcome.ASK
    await tools.decide_call(owner, session, found.request.seq, approve=True)
    gate = await tools.gate(
        owner, registry, KIND_DEFAULTS, found.request, found.call_input, workspace
    )
    assert gate.outcome is GateOutcome.RUN

    response = await tools.execute(
        owner,
        registry,
        found.request,
        found.call_input,
        workspace,
        epoch=found.epoch,
        tree_deadline=None,
    )
    assert failure_of(response) is None
    assert f"token {marker('api_token')}" in result_text(response)
    events = await managers.events.get_events(owner, 0, 100)
    (audit,) = [event for event in events if event.kind == SECRET_USED]
    assert audit.payload["secret"] == "api_token" and audit.target_id == found.request.id
    held = [response.model_dump_json(), *(event.model_dump_json() for event in events)]
    held += [
        step.model_dump_json() for step in (await steps.get_steps(owner, session, 0, 100)).items
    ]
    for text in held:
        for form in forms(SECRET):
            assert form not in text

    # An unsafe call whose command ended, and whose run was lost before its
    # response was written: the next run answers it from the record.
    deploy = await put_call(
        tools,
        steps,
        owner,
        "deploy",
        {"argv": ["sh", "-c", "echo deployed >> deploys.log; echo done"]},
        "execute",
        session_id=session,
        epoch=found.epoch,
    )
    await tools.execute(
        owner,
        registry,
        deploy.request,
        deploy.call_input,
        workspace,
        epoch=deploy.epoch,
        tree_deadline=None,
    )
    later = await steps.begin_run(owner, session)
    settled = await tools.recover(
        owner,
        registry,
        deploy.request,
        deploy.call_input,
        workspace,
        epoch=later,
        tree_deadline=None,
    )
    assert failure_of(settled) is None and "transport's record" in result_text(settled)
    assert "done" in result_text(settled)
    assert (Path(workspace.location) / "deploys.log").read_text() == "deployed\n"
    record = tmp_path / "workspaces" / ".records" / session.hex / f"{deploy.request.id}.json"
    assert b"done" not in record.read_bytes(), "what it printed is sealed at rest"
