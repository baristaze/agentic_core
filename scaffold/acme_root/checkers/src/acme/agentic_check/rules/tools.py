"""The tools group: what a tool declares, what it may carry, and where it may reach."""

from __future__ import annotations

import ast
from collections.abc import Iterator

from acme.agentic_check.model import Violation
from acme.agentic_check.project import Project, SourceFile, is_under, names_in
from acme.agentic_check.registry import rule

DECLARED = ("authorization_class", "effect", "timeout", "interruptible", "mode")
"""What every tool names when it builds its spec: the fields a review reads, never left to a default."""
SECRET_TYPES = frozenset({"SecretStr", "SecretBytes"})
"""The types that carry a secret's value."""
HOST_MODULES = ("subprocess", "socket", "shutil", "pty", "multiprocessing", "asyncio.subprocess")
"""Modules that start a process, open a socket, or touch files on the host the code runs on."""
HOST_CALLS = (
    "os.system",
    "os.popen",
    "os.fork",
    "os.exec",
    "os.spawn",
    "os.posix_spawn",
    "os.open",
    "io.open",
)
"""Calls, by prefix, that start a process or open a file on the host."""
ASYNC_PROCESSES = ("asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell")


def one(project: Project, namespace: str, name: str) -> str | None:
    """The full name of the class `name` under the OM namespace, when exactly one is defined there."""
    found = project.defined(project.sub(f"om.{namespace}"), name)
    return found[0] if len(found) == 1 else None


def spec_calls(project: Project) -> Iterator[tuple[SourceFile, ast.Call]]:
    """Every call in the source that builds the tools namespace's `ToolSpec`."""
    spec = one(project, "tools", "ToolSpec")
    if spec is None:
        return
    for file, tree in project.trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and project.full_name(file, node.func) == spec:
                yield file, node


@rule(
    "TOL-01",
    coverage="partial",
    summary="Every ToolSpec the source builds without unpacking a mapping names its class, effect, timeout, "
    "interruptibility, and mode by keyword.",
)
def every_tool_declares_its_contract(project: Project) -> Iterator[Violation]:
    """Each call of the tools namespace's `ToolSpec` passes
    `authorization_class`, `effect`, `timeout`, `interruptible`, and
    `mode` as keywords, so a tool never takes its mode from a default
    and a review reads each one at the tool. A call that unpacks a
    mapping (`**fields`) cannot be read, and is judged. The schema that
    refuses unknown fields is the guideline's checker's (OM-07), and
    whether the declared values are true to the tool is judged."""
    for file, call in spec_calls(project):
        if any(k.arg is None for k in call.keywords):
            continue
        given = {k.arg for k in call.keywords}
        missing = [name for name in DECLARED if name not in given]
        if missing:
            yield Violation.at(
                file.rel,
                call,
                f"builds a ToolSpec without {', '.join(missing)}; every tool declares each by keyword",
            )


def secret_fields(project: Project, name: str) -> Iterator[tuple[SourceFile, ast.AnnAssign, str]]:
    """Each field of a class whose annotation names a secret's type."""
    file, cls = project.classes[name]
    for node in cls.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            kinds = sorted(SECRET_TYPES & set(names_in(node.annotation)))
            if kinds:
                yield file, node, kinds[0]


@rule(
    "TOL-11",
    coverage="partial",
    summary="No tool input or output, and no type of the steps namespace, "
    "declares a field typed SecretStr or SecretBytes.",
)
def no_secret_value_in_a_step(project: Project) -> Iterator[Violation]:
    """A tool's input and its output are written into the steps of its
    call, and a step type is the step. So none of them declares a field
    whose annotation names `SecretStr` or `SecretBytes`: a tool names a
    secret and never carries its value. The input is every class that
    derives from the tools namespace's `ToolInput`; the output, every
    class a `ToolSpec` names as its `output_model`; the step types,
    every class under the steps namespace. How a credential reaches a
    tool's process, and what the audit records, are judged."""
    carriers: set[str] = set()
    tool_input = one(project, "tools", "ToolInput")
    if tool_input is not None:
        carriers |= project.subclasses(tool_input)
    for file, call in spec_calls(project):
        for k in call.keywords:
            if (
                k.arg == "output_model"
                and (full := project.full_name(file, k.value)) in project.classes
            ):
                carriers.add(full)
    steps = project.sub("om.steps")
    carriers |= {n for n, (f, _) in project.classes.items() if is_under(f.module, steps)}
    for name in sorted(carriers):
        for file, node, kind in secret_fields(project, name):
            field = node.target.id if isinstance(node.target, ast.Name) else "a field"
            yield Violation.at(
                file.rel,
                node,
                f"{name.rpartition('.')[2]}.{field} is a {kind}; a secret's value would be written into a step, "
                "and a tool names a secret instead",
            )


@rule(
    "TOL-13",
    coverage="partial",
    summary="A module that defines a tool imports no process, socket, or shutil module, and calls no open() and no os "
    "or asyncio function that starts a process.",
)
def tools_reach_only_through_the_transport(project: Project) -> Iterator[Violation]:
    """A module that defines a tool, a class deriving from the tools
    namespace's `ToolInterface` whose name does not end in `Interface`,
    imports none of `subprocess`, `socket`, `shutil`, `pty`,
    `multiprocessing`, and `asyncio.subprocess`, and calls neither
    `open()` nor an `os` or `asyncio` function that starts a process or
    opens a file. A tool reaches its workspace through the runtime its
    call is given. A path that reaches the host another way, such as
    `pathlib`, is judged."""
    interface = one(project, "tools", "ToolInterface")
    if interface is None:
        return
    tools = {n for n in project.subclasses(interface) if not n.endswith("Interface")}
    homes = sorted({project.classes[n][0].rel for n in tools})
    for file, tree in project.trees():
        if file.rel not in homes:
            continue
        bound = project.bound_names(file)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                if isinstance(node, ast.ImportFrom):
                    target = (
                        project.resolve(file, node.level, node.module)
                        if node.level
                        else node.module or ""
                    )
                    imported = [target, *(f"{target}.{a.name}" for a in node.names)]
                else:
                    imported = [a.name for a in node.names]
                hit = next((m for m in imported if any(is_under(m, h) for h in HOST_MODULES)), None)
                if hit is not None:
                    yield Violation.at(
                        file.rel,
                        node,
                        f"imports {hit}; a tool runs commands and touches files only through its runtime",
                    )
            elif isinstance(node, ast.Call):
                full = project.full_name(file, node.func)
                if (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "open"
                    and "open" not in bound
                ):
                    full = "open"
                if full and (
                    full == "open" or full.startswith(HOST_CALLS) or full in ASYNC_PROCESSES
                ):
                    yield Violation.at(
                        file.rel,
                        node,
                        f"calls {full}; a tool runs commands and touches files only through its runtime",
                    )
