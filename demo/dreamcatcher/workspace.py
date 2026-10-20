"""The small workspace the agent works in, and its three tools.

The workspace is a few files in memory. Its check is real: it parses
`sizes.py` with `ast` and compares the sum of the sizes with the total,
so an edit changes what the check says. Nothing here runs a process,
executes what the model wrote, or reaches a network.

Each tool declares its class and its effect, as the engine asks. The
edit is `idempotent` under the call's key: the engine runs it again
under the same key after a lost run, and the workspace applies it once.
"""

import ast
import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

from acme.om.agent_steps.types.header import ToolFailure
from acme.om.base import OmBase
from acme.om.context import TenantContext
from acme.om.exceptions import ToolFailed
from acme.om.tools.tool import ToolInterface, ToolRuntime
from acme.om.tools.types.policy import Target
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolSpec

FILES = {
    "sizes.py": 'SIZES = {"small": 3, "medium": 4, "large": 5}\nTOTAL = 13\n',
    "NOTES.md": "# Notes\n",
}
"""The workspace as the run finds it: a total that does not match."""


@dataclass
class Cue:
    """A call the director holds open: `started` is set inside the call,
    and the call goes on once `release` is set."""

    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class Workspace:
    files: dict[str, str] = field(default_factory=lambda: dict(FILES))
    applied: dict[str, "Edited"] = field(default_factory=dict)
    """Each edit applied, by the key of the call that applied it."""
    attempts: list[str] = field(default_factory=list)
    """The key of every edit call, each time it ran."""
    cues: dict[str, Cue] = field(default_factory=dict)
    pause: float = 0.0
    """Seconds each call takes, so a person can follow the run."""

    def cue(self, tool: str) -> Cue:
        """Hold the next call of `tool` open until the cue's release."""
        self.cues[tool] = Cue()
        return self.cues[tool]

    async def wait(self, tool: str) -> None:
        if self.pause:
            await asyncio.sleep(self.pause)
        cue = self.cues.pop(tool, None)
        if cue is not None:
            cue.started.set()
            await cue.release.wait()

    def check(self) -> "Checked":
        """`sum(SIZES.values()) == TOTAL`, read from the source by `ast`."""
        values: dict[str, object] = {}
        tree = ast.parse(self.files.get("sizes.py", ""))
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                values[node.targets[0].id] = ast.literal_eval(node.value)
        sizes, total = values.get("SIZES"), values.get("TOTAL")
        if not isinstance(sizes, dict) or not isinstance(total, int):
            return Checked(passed=False, detail="sizes.py holds no SIZES dict and TOTAL int")
        summed = sum(int(v) for v in sizes.values())
        if summed == total:
            return Checked(passed=True, detail=f"1 passed: sum(SIZES) == TOTAL == {total}")
        return Checked(passed=False, detail=f"1 failed: sum(SIZES) is {summed}, TOTAL is {total}")


class Path(ToolInput):
    path: str


class Read(OmBase):
    path: str
    text: str


class NoInput(ToolInput):
    pass


class Checked(OmBase):
    passed: bool
    detail: str


class Edit(ToolInput):
    path: str
    old: str
    new: str


class Edited(OmBase):
    path: str
    replayed: bool = False
    """True when the key was already applied: this call found the edit made."""


def _spec(name: str, about: str, inp: type, out: type, cls: str, effect: Effect) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=about,
        input_model=inp,
        output_model=out,
        timeout=timedelta(minutes=1),
        authorization_class=cls,
        effect=effect,
        interruptible=True,
    )


class _Tool(ToolInterface):
    def __init__(self, workspace: Workspace, spec: ToolSpec) -> None:
        self._workspace = workspace
        self._spec = spec

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def target(self, ctx: TenantContext, call_input: ToolInput) -> Target:
        return Target()

    async def preflight(self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime) -> None:
        return None


class ReadFile(_Tool):
    def __init__(self, workspace: Workspace) -> None:
        about = "Reads a file of the workspace."
        super().__init__(workspace, _spec("read_file", about, Path, Read, ToolClass.READ, Effect.READ_ONLY))

    async def run(self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime) -> OmBase:
        assert isinstance(call_input, Path)
        await self._workspace.wait(self._spec.name)
        return Read(path=call_input.path, text=self._workspace.files.get(call_input.path, ""))


class RunCheck(_Tool):
    def __init__(self, workspace: Workspace) -> None:
        about = "Runs the workspace's check."
        super().__init__(workspace, _spec("run_check", about, NoInput, Checked, ToolClass.EXECUTE, Effect.READ_ONLY))

    async def run(self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime) -> OmBase:
        await self._workspace.wait(self._spec.name)
        return self._workspace.check()


class EditFile(_Tool):
    def __init__(self, workspace: Workspace) -> None:
        about = "Replaces the one occurrence of `old` with `new` in a file."
        super().__init__(workspace, _spec("edit_file", about, Edit, Edited, ToolClass.WRITE, Effect.IDEMPOTENT))

    async def run(self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime) -> OmBase:
        assert isinstance(call_input, Edit)
        key = str(runtime.key)
        self._workspace.attempts.append(key)
        done: Edited | None = self._workspace.applied.get(key)
        if done is not None:
            await self._workspace.wait(self._spec.name)
            return done.model_copy(update={"replayed": True})
        text = self._workspace.files.get(call_input.path, "")
        if text.count(call_input.old) != 1:
            detail = f"{call_input.old!r} is not in {call_input.path} exactly once"
            raise ToolFailed(ToolFailure.INVALID_INPUT, detail)
        self._workspace.files[call_input.path] = text.replace(call_input.old, call_input.new)
        self._workspace.applied[key] = Edited(path=call_input.path)
        # The effect is made before the call answers: a run lost here leaves
        # it applied, and the run that takes over finds it under the key.
        await self._workspace.wait(self._spec.name)
        return self._workspace.applied[key]


def tools(workspace: Workspace) -> tuple:
    return (ReadFile(workspace), RunCheck(workspace), EditFile(workspace))
