"""The example assistant: the local and the staging stacks offer it, and no
other. Asked a question, it reads the org's files in the request and
answers there, with no call a person approves."""

import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from api_support import build_container

from acme.integrations.identity.twin import IdentityProviderTwinImpl
from acme.integrations.impl.configured import IntegrationsOverImpl
from acme.integrations.model_providers import ModelProvidersInterface
from acme.integrations.model_providers.registry import scripted_model_providers
from acme.integrations.model_providers.scripted import ModelProviderScriptedImpl
from acme.integrations.model_providers.types import ProviderName
from acme.services.api.assistant import ASSISTANT, LIST_FILES, offered
from acme.services.api.container import AppContainer

from contracts.loops import call, reply, said
from contracts.media_storage import make_file


@pytest.fixture
def providers() -> ModelProvidersInterface:
    return scripted_model_providers()


@pytest.fixture
def container(tmp_path: Path, providers: ModelProvidersInterface) -> AppContainer:
    return build_container(
        tmp_path,
        integrations=IntegrationsOverImpl(IdentityProviderTwinImpl(), providers),
        environment="local",
    )


@pytest.mark.parametrize(
    ("environment", "kinds"),
    [("local", ("assistant",)), ("staging", ("assistant",)), ("production", ()), ("test", ())],
)
def test_the_assistant_is_offered_in_the_local_and_staging_stacks_alone(
    environment: str, kinds: tuple[str, ...]
) -> None:
    def never() -> object:
        raise AssertionError("no tool is built that reads nothing")

    offered_kinds, offered_tools = offered(environment, never)  # pyright: ignore[reportArgumentType]

    assert tuple(kind.name for kind in offered_kinds) == kinds
    assert tuple(tool.spec.name for tool in offered_tools) == ((LIST_FILES,) if kinds else ())


async def test_the_assistant_reads_the_orgs_files_and_answers_in_the_request(
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    providers: ModelProvidersInterface,
) -> None:
    org = await client.get("/v1/orgs/current", headers=owner)
    org_id = UUID(org.json()["id"])
    await container.storage.get_media_storage().create_file(org_id, make_file(), ())
    twin = providers.get(ProviderName.ANTHROPIC)
    assert isinstance(twin, ModelProviderScriptedImpl)
    twin.add(reply(call(LIST_FILES)), reply(said("The org keeps one file, notes.pdf.")))

    asked = await client.post(
        "/v1/agent-sessions/questions",
        headers=owner,
        json={"kind": ASSISTANT.name, "title": "files", "text": "Which files do we keep?"},
    )

    assert asked.status_code == 200, asked.text
    frames = [f for f in asked.text.split("\n\n") if f.strip()]
    end = json.loads(frames[-1].split("data: ", 1)[1])
    assert (end["end"], end["outcome"]) == ("ended", "succeeded")
    steps = await client.get(f"/v1/agent-sessions/{end['agent_session_id']}/steps", headers=owner)
    (read,) = [s for s in steps.json()["items"] if s["type"] == "tool_response"]
    assert read["failure"] is None and "notes.pdf" in read["text"]
