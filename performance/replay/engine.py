"""The engine as the session runner builds it, in this process.

`RunnerContainer.over` is the runner's own wiring: the managers over the
storage, the infra, and the integrations, with the product's kinds and
tools. On Postgres, every root is built from the runner's settings as its
`build` builds them, read from the environment `stack.env` sets: each role
on its own database, tool outputs in the object store, the cache on
Valkey, and the key service the configuration names, the KMS adapter at the
emulator or the in-process twin. On memory, the storage is the memory one
and the infra the local twins, as the demo runs. The model is the scripted
twin, on either.

Three things are the harness's and change nothing the engine does: the key
service is counted on its way in, each append of the sealing layer is
timed, and the twin answers each session from that session's script."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter, deque
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from acme.infra.impl.configured import InfraConfiguredImpl
from acme.infra.impl.local import InfraLocalImpl
from acme.infra.keys import DataKey, KeyServiceInterface, WrappedKey
from acme.infra.root import InfraInterface
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.integrations.impl.configured import IntegrationsOverImpl
from acme.integrations.model_providers.calls import Finished, ModelCall, ModelReply, StreamPart
from acme.integrations.model_providers.content import TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock
from acme.integrations.model_providers.registry import ModelProvidersOverImpl
from acme.integrations.model_providers.scripted import ModelProviderScriptedImpl, parts_of
from acme.integrations.model_providers.types import ProviderName, StopReason, Usage
from acme.om.agent_steps.types.agent_step import Actor, AgentStep, AgentStepType, Origin
from acme.om.agent_steps.types.content import Content
from acme.om.agent_steps.types.content import TextBlock as StepText
from acme.om.agent_steps.types.header import InputHeader
from acme.om.agents.types.kind import AgentKind, DoneRule
from acme.om.agents.types.request import Start
from acme.om.agents.types.run import LoopRun, RunEnd
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.base import new_id, utcnow
from acme.om.context import AppContext, AppType, RequestContext, TenantContext
from acme.om.privacy.impl.sealed_agent_steps import AgentStepStorageSealedImpl
from acme.om.root import Managers
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.storage.impl.postgres import StoragePostgresImpl
from acme.om.storage.root import StorageInterface
from acme.om.tools.types.policy import Decision, ToolPolicyLayer, ToolPolicyRule
from acme.workers.agent_session_runner.container import RunnerContainer
from acme.workers.agent_session_runner.settings import AgentSessionRunnerSettings
from replay.profile import qualname
from replay.shape import TOOL_CLASSES
from replay.tools import CLASSES, tool_name, tools
from replay.words import Words

MAIN_MODEL = "claude-sonnet-5-5"
"""The main fill's model, as the engine's default table names it."""
CHARS_PER_TOKEN = 4
"""The engine's own estimate, so the twin's usage compacts where the
engine's estimate would."""
SUMMARY_CHARS = 16_000
APP = AppContext(type=AppType.CLI, version="performance@1")

KIND = AgentKind(
    name="replayed",
    version=1,
    tools=tuple(tool_name(c) for c in TOOL_CLASSES),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    prompts=("You work through a long task in a workspace, one tool call at a time.",),
    policy=ToolPolicyLayer(rules=tuple(ToolPolicyRule(authorization_class=c, decision=Decision.ALLOW) for c in CLASSES)),
)

SESSION: ContextVar[UUID] = ContextVar("the replayed session")
"""The session a model call is made for: each session's runs set it, so the
one twin answers each from its own script."""


@dataclass
class Counts:
    """What the harness counts as the engine runs: each key service call by
    the engine function that asked for it, and each append's latency."""

    keys: Counter[str] = field(default_factory=Counter)
    appends: list[float] = field(default_factory=list)
    steps_written: int = 0

    def key_calls(self) -> int:
        return sum(self.keys.values())


class Twin(ModelProviderScriptedImpl):
    """The scripted twin, one script per session. Its usage says the size
    of the request it was sent, at the engine's own rate, so the window
    compacts where it would; a summarizer's call is answered with a summary,
    outside every script. It keeps no call it was asked."""

    def __init__(self, provider: ProviderName, words: Words) -> None:
        super().__init__(provider)
        self._words = words
        self.scripts: dict[UUID, deque[ModelReply]] = {}
        self.streamed: dict[UUID, list[ModelReply]] | None = None
        self.summaries = 0
        self.answered = 0
        self.on_answer: Callable[[int], None] | None = None

    async def stream(self, call: ModelCall, *, credential: object = None) -> AsyncIterator[StreamPart]:
        session = SESSION.get()
        if call.model != MAIN_MODEL:
            self.summaries += 1
            reply = ModelReply(
                blocks=(TextBlock(text=self._words.text(SUMMARY_CHARS)),),
                stop_reason=StopReason.END_TURN,
                usage=Usage(),
                model=call.model,
            )
        else:
            reply = self.scripts[session].popleft()
            self.answered += 1
            if self.on_answer is not None:
                self.on_answer(self.answered)
        said = sum(len(b.text) for b in reply.blocks if isinstance(b, TextBlock))
        thought = sum(len(t.text) for t in reply.thinking)
        reply = reply.model_copy(
            update={
                "usage": Usage(
                    input=max(1, request_chars(call) // CHARS_PER_TOKEN),
                    output=max(1, said // CHARS_PER_TOKEN),
                    thinking=thought // CHARS_PER_TOKEN,
                )
            }
        )
        if self.streamed is not None:
            self.streamed.setdefault(session, []).append(reply)
        for part in parts_of(reply):
            yield part
        yield Finished(reply=reply)


def request_chars(call: ModelCall) -> int:
    """The characters a request carries, as the engine's estimate counts
    them: its text, its thinking, its tool inputs, and its results."""
    total = sum(len(block.text) for block in call.system)
    for message in call.messages:
        for block in message.blocks:
            if isinstance(block, (TextBlock, ThinkingBlock)):
                total += len(block.text)
            elif isinstance(block, ToolUseBlock):
                total += len(json.dumps(dict(block.input), default=str))
            elif isinstance(block, ToolResultBlock):
                total += sum(len(part.text) for part in block.parts if isinstance(part, TextBlock))
    return total


class CountedKeys(KeyServiceInterface):
    """The key service, counted: each call by the engine function that asked
    for it, the first frame outside the key service and the session keys."""

    PASSED = ("/acme/infra/keys/", "/acme/om/privacy/impl/keys.py", "/performance/replay/engine.py")

    def __init__(self, inner: KeyServiceInterface, counts: Counts) -> None:
        self._inner = inner
        self._counts = counts

    def _count(self, call: str) -> None:
        frame = sys._getframe(2)
        while frame is not None and any(part in frame.f_code.co_filename for part in self.PASSED):
            frame = frame.f_back  # type: ignore[assignment]
        asker = "?" if frame is None else f"{_short(frame.f_code.co_filename)}:{qualname(frame.f_code)}"
        self._counts.keys[f"{call} for {asker}"] += 1

    async def generate(self, org_id: UUID, key_id: UUID, version: int) -> DataKey:
        self._count("generate")
        return await self._inner.generate(org_id, key_id, version)

    async def unwrap(self, org_id: UUID, key_id: UUID, version: int, wrapped: WrappedKey) -> bytes:
        self._count("unwrap")
        return await self._inner.unwrap(org_id, key_id, version, wrapped)

    async def rewrap(self, org_id: UUID, key_id: UUID, version: int, wrapped: WrappedKey) -> WrappedKey:
        self._count("rewrap")
        return await self._inner.rewrap(org_id, key_id, version, wrapped)

    def describe(self) -> str:
        return self._inner.describe()

    async def start(self) -> None:
        await self._inner.start()

    async def close(self) -> None:
        await self._inner.close()


def _short(path: str) -> str:
    at = path.rfind("/acme/")
    return path[at + 1 :] if at >= 0 else path


@contextmanager
def timed_appends(counts: Counts) -> Iterator[None]:
    """Each append of the sealing layer, timed from its call to its return:
    the seal, the write, and the open of what was stored."""
    originals = {name: getattr(AgentStepStorageSealedImpl, name) for name in ("append_agent_steps", "append_inputs")}

    def timing(original: Callable[..., Any]) -> Callable[..., Any]:
        async def append(self: AgentStepStorageSealedImpl, *args: Any) -> tuple[AgentStep, ...]:
            started = time.perf_counter()
            stored: tuple[AgentStep, ...] = await original(self, *args)
            counts.appends.append(time.perf_counter() - started)
            counts.steps_written += len(stored)
            return stored

        return append

    for name, original in originals.items():
        setattr(AgentStepStorageSealedImpl, name, timing(original))
    try:
        yield
    finally:
        for name, original in originals.items():
            setattr(AgentStepStorageSealedImpl, name, original)


def counted(infra: InfraInterface, counts: Counts) -> InfraInterface:
    keys = CountedKeys(infra.get_keys(), counts)
    infra.get_keys = lambda: keys
    return infra


class Engine:
    """The managers as the runner builds them, and the harness's handle on
    them: a tenant and its owner, sessions started and spoken to as the
    person, and runs of their loops."""

    def __init__(
        self, storage: StorageInterface, infra: InfraInterface, settings: AgentSessionRunnerSettings, words: Words
    ) -> None:
        self.counts = Counts()
        self.storage = storage
        self.infra = counted(infra, self.counts)
        self.twins = {p: Twin(p, words) for p in ProviderName}
        integrations = IntegrationsOverImpl(IdentityProviderAbsentImpl(), ModelProvidersOverImpl(self.twins))
        self.container = RunnerContainer.over(
            settings, storage, self.infra, integrations, agent_kinds=(KIND,), tool_catalog=tools(words)
        )
        self.managers: Managers = self.container.managers
        self.owner: TenantContext | None = None

    @classmethod
    def on_postgres(cls, keys: str, words: Words) -> Engine:
        """Over the stack `stack.env` names, with `keys` the key service:
        `kms`, the adapter at the emulator, or `memory`, the twin."""
        settings = AgentSessionRunnerSettings(_env_file=None, keys_backend=keys)
        storage = StoragePostgresImpl(settings.role_urls(), settings.role_pools(), system_urls=settings.system_role_urls())
        return cls(storage, InfraConfiguredImpl(settings), settings, words)

    @classmethod
    def on_memory(cls, folder: Path, words: Words) -> Engine:
        settings = AgentSessionRunnerSettings(_env_file=None, environment="local")
        return cls(StorageMemoryImpl(), InfraLocalImpl(folder), settings, words)

    @property
    def twin(self) -> Twin:
        return self.twins[ProviderName.ANTHROPIC]

    async def start(self) -> None:
        await self.container.start()
        slug = f"performance-{new_id().hex[-8:]}"
        self.owner, _ = await self.managers.tenancy.bootstrap(
            RequestContext(request_id=new_id(), app=APP), "Performance", slug, f"owner-{slug}@example.test", "Owner"
        )

    async def close(self) -> None:
        await self.container.close()

    def ctx(self) -> TenantContext:
        assert self.owner is not None
        return self.owner

    async def new_session(self) -> UUID:
        session = await self.managers.agents.start_agent_session(self.ctx(), Start(id=new_id(), kind=KIND.name, title="replay"))
        return session.id

    async def say(self, agent_session_id: UUID, text: str) -> None:
        step_id = new_id()
        owner = self.ctx()
        message = AgentStep(
            id=step_id,
            created_at=utcnow(),
            agent_session_id=agent_session_id,
            loop_id=step_id,
            type=AgentStepType.MESSAGE,
            actor=Actor.PERSON,
            origin=Origin.PORTAL,
            header=InputHeader(principal=Principal(kind=PrincipalKind.PERSON, id=owner.user_id)),
            content=Content(blocks=(StepText(text=text),)),
        )
        await self.managers.agent_steps.append_inputs(owner, agent_session_id, [message])

    async def run(self, agent_session_id: UUID) -> LoopRun:
        """The loop run to its end, as the runner's claims would run it: a run
        whose time ran out is run again."""
        while True:
            run = await self.managers.loop.run(self.ctx(), agent_session_id)
            if run.end is not RunEnd.YIELDED:
                return run

    async def history(self, agent_session_id: UUID) -> list[AgentStep]:
        steps: list[AgentStep] = []
        while True:
            after = steps[-1].seq if steps else 0
            page = await self.managers.agent_steps.get_agent_steps(self.ctx(), agent_session_id, after, 200)
            steps.extend(page.items)
            if not page.has_more or not page.items:
                return steps


async def gather_sessions(work: list[Any]) -> list[Any]:
    return list(await asyncio.gather(*work))
