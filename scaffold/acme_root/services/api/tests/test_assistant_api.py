"""The example assistant: the local and the staging stacks offer it, and no
other. Asked a question, it reads the org's files in the request and
answers there, with no call a person approves. Its kind is a folder of its
own, whose prompt file renders the text its constant held."""

import importlib
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
from acme.om.agents.prompts import prompt_breaches
from acme.om.agents.types.kind import AgentKind
from acme.services.api import kinds
from acme.services.api.assistant import offered
from acme.services.api.container import AppContainer
from acme.services.api.kinds.assistant.kind import ASSISTANT
from acme.services.api.list_files import LIST_FILES

from contracts.loops import call, reply, said
from contracts.media_storage import make_file

LITERAL = (
    "You answer a member's questions about the files their org keeps. Read "
    "them with list_files, and answer in a few sentences. When the files do "
    "not hold the answer, say so."
)
"""The assistant's prompt as its constant held it, before its file did."""

FOLDERS = sorted(
    folder for folder in Path(kinds.__file__).parent.iterdir() if (folder / "kind.py").is_file()
)
"""The example's kind folders, each beside the others."""


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
    ("environment", "declared", "kinds"),
    [
        ("local", (), ("assistant",)),
        ("staging", (), ("assistant",)),
        ("production", (), ()),
        ("test", (), ()),
        ("local", (ASSISTANT.model_copy(update={"name": "product"}),), ()),
    ],
)
def test_the_assistant_is_offered_in_the_local_and_staging_stacks_alone(
    environment: str, declared: tuple[AgentKind, ...], kinds: tuple[str, ...]
) -> None:
    """A product that declares its own kinds is offered none of the example."""

    def never() -> object:
        raise AssertionError("no tool is built that reads nothing")

    offered_kinds, offered_tools = offered(environment, declared, never)  # pyright: ignore[reportArgumentType]

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


@pytest.mark.parametrize("folder", FOLDERS, ids=lambda folder: folder.name)
def test_each_kind_folder_holds_the_prompt_of_each_version_as_its_lock_pins_it(
    folder: Path,
) -> None:
    module = importlib.import_module(f"{kinds.__name__}.{folder.name}.kind")

    assert FOLDERS, "the example declares its kinds as folders"
    assert prompt_breaches(folder, module.VERSIONS) == ()


async def test_the_assistant_renders_its_prompt_file_as_its_constant_rendered_its_text(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    providers: ModelProvidersInterface,
) -> None:
    twin = providers.get(ProviderName.ANTHROPIC)
    assert isinstance(twin, ModelProviderScriptedImpl)
    twin.add(reply(said("The org keeps no file.")))

    asked = await client.post(
        "/v1/agent-sessions/questions",
        headers=owner,
        json={"kind": ASSISTANT.name, "title": "files", "text": "Which files do we keep?"},
    )

    assert asked.status_code == 200, asked.text
    (call,) = twin.calls
    assert ASSISTANT.prompts == (LITERAL,)
    assert call.system[0].text.encode() == LITERAL.encode()
