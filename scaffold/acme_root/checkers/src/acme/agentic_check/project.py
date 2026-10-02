"""The project under check, as a parsed tree a rule reads.

`Project` finds the Python files under the configured source roots,
names each one's module the way the import system would, and parses a
file with `ast` the first time a rule asks for it. It resolves a name a
file uses to the module that defines it, through the file's imports and
any package that re-exports it, so a rule names a class by where it is
defined. Nothing here imports the code it reads.

The module-level functions are the `ast` helpers rules share.
"""

from __future__ import annotations

import ast
import contextlib
from collections.abc import Collection, Iterator
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TypeVar

from acme.agentic_check.config import Config, ConfigError, glob_match

T = TypeVar("T")

SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
    }
)
"""Directory names never walked into, at any depth."""

Function = ast.FunctionDef | ast.AsyncFunctionDef

MAX_HOPS = 8
"""The most re-exports `canonical` follows before it gives a name up as unresolved."""


@dataclass(frozen=True)
class SourceFile:
    """A Python file under a source root: its path from the root with `/`, and the module it imports as."""

    path: Path
    rel: str
    module: str
    is_package: bool


class Project:
    """The repository at `config.root`, read through `config`."""

    def __init__(self, config: Config, declared: dict[str, set[str]] | None = None) -> None:
        self.config = config
        self.declared = declared or {}
        """Every option key each rule declares, by id."""
        self.root = config.root
        self.package = config.package
        self.parse_errors: dict[str, tuple[int, str]] = {}
        self._trees: dict[str, ast.Module | None] = {}
        self._bound: dict[str, dict[str, str]] = {}

    # --- options

    def option(self, rule: str, key: str, default: T, allowed: Collection[str]) -> T:
        """The project's value of `key` under `[tool.agentic-check.options.<rule>]`, else `default`.

        Any key the rule does not read is a `ConfigError`, so a misspelt
        option never falls back to the default. The value has the
        default's type, and a list holds strings only.
        """
        entries = self.config.options.get(rule, {})
        unknown = sorted(set(entries) - set(allowed) - self.declared.get(rule, set()))
        if unknown:
            raise ConfigError(
                f"[tool.agentic-check.options.{rule}]: unknown key(s) {', '.join(unknown)}"
            )
        if key not in entries:
            return default
        value = entries[key]
        if not isinstance(value, type(default)) or (
            isinstance(value, list) and not all(isinstance(v, str) for v in value)
        ):
            raise ConfigError(
                f"[tool.agentic-check.options.{rule}] `{key}` must be a {type(default).__name__}"
            )
        return value

    # --- names

    def sub(self, name: str) -> str:
        """A subpackage of the product: `sub("om.tools")` is `acme.om.tools`."""
        return f"{self.package}.{name}"

    def rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.resolve().relative_to(self.root).as_posix()

    def excluded(self, rel: str) -> bool:
        parts = rel.split("/")
        if any(part in SKIP_DIRS for part in parts[:-1]):
            return True
        return any(glob_match(pattern, rel) for pattern in self.config.exclude)

    # --- Python files

    @cached_property
    def python_files(self) -> tuple[SourceFile, ...]:
        """Every `.py` file under a source root, excluded ones left out, in path order."""
        roots: set[Path] = set()
        for pattern in self.config.src:
            roots.update(p for p in self.root.glob(pattern) if p.is_dir())
        out: dict[str, SourceFile] = {}
        for src in sorted(roots):
            for path in sorted(src.rglob("*.py")):
                rel = self.rel(path)
                if rel in out or self.excluded(rel) or not path.is_file():
                    continue
                parts = list(path.relative_to(src).with_suffix("").parts)
                is_package = parts[-1] == "__init__"
                if is_package:
                    parts.pop()
                if parts:
                    out[rel] = SourceFile(
                        path=path, rel=rel, module=".".join(parts), is_package=is_package
                    )
        return tuple(out[k] for k in sorted(out))

    @cached_property
    def by_module(self) -> dict[str, SourceFile]:
        out: dict[str, SourceFile] = {}
        for f in self.python_files:
            out.setdefault(f.module, f)
        return out

    def modules_under(self, *prefixes: str) -> list[SourceFile]:
        """The files whose module is one of `prefixes` or below one, in path order."""
        return [f for f in self.python_files if any(is_under(f.module, p) for p in prefixes)]

    def tree(self, file: SourceFile) -> ast.Module | None:
        """The parsed module, cached; None when it does not parse (see `parse_errors`)."""
        if file.rel not in self._trees:
            try:
                self._trees[file.rel] = ast.parse(file.path.read_bytes(), filename=file.rel)
            except SyntaxError as e:
                self._trees[file.rel] = None
                self.parse_errors[file.rel] = (e.lineno or 1, e.msg)
            except (OSError, ValueError, RecursionError, MemoryError) as e:
                self._trees[file.rel] = None
                self.parse_errors[file.rel] = (1, str(e) or type(e).__name__)
        return self._trees[file.rel]

    def trees(self, *prefixes: str) -> Iterator[tuple[SourceFile, ast.Module]]:
        """(file, tree) for every file that parses, under `prefixes` when any are given."""
        for f in self.modules_under(*prefixes) if prefixes else self.python_files:
            t = self.tree(f)
            if t is not None:
                yield f, t

    def parse_all(self) -> None:
        for f in self.python_files:
            self.tree(f)

    # --- imports and the names they bind

    def resolve(self, file: SourceFile, level: int, module: str | None) -> str:
        """The absolute module a relative import names, as the import system resolves it."""
        package = file.module if file.is_package else file.module.rpartition(".")[0]
        parts = package.split(".") if package else []
        if level > 1:
            parts = parts[: max(0, len(parts) - (level - 1))]
        base = ".".join(parts)
        if module:
            return f"{base}.{module}" if base else module
        return base

    def bound_names(self, file: SourceFile) -> dict[str, str]:
        """Each name a file binds at its top level to the dotted name it means.

        An import binds what it imports (`from a.b import C as D` gives
        `D -> a.b.C`, `import a.b` gives `a -> a`), and a class or a
        function the file defines binds `<module>.<name>`.
        """
        if file.rel in self._bound:
            return self._bound[file.rel]
        out: dict[str, str] = {}
        tree = self.tree(file)
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    target = (
                        self.resolve(file, node.level, node.module)
                        if node.level
                        else node.module or ""
                    )
                    for alias in node.names:
                        if alias.name != "*":
                            out[alias.asname or alias.name] = f"{target}.{alias.name}"
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        out[alias.asname or alias.name.split(".")[0]] = (
                            alias.name if alias.asname else alias.name.split(".")[0]
                        )
            for node in tree.body:
                if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                    out[node.name] = f"{file.module}.{node.name}"
        self._bound[file.rel] = out
        return out

    def full_name(self, file: SourceFile, node: ast.AST | None) -> str | None:
        """The dotted name an expression in `file` means, read through its bindings and re-exports; else None."""
        name = dotted(node)
        if name is None:
            return None
        head, _, rest = name.partition(".")
        target = self.bound_names(file).get(head)
        if target is None:
            return None
        return self.canonical(f"{target}.{rest}" if rest else target)

    def canonical(self, name: str) -> str:
        """Where a dotted name is defined: a name a package re-exports is followed to the module that defines it."""
        for _ in range(MAX_HOPS):
            if name in self.classes:
                return name
            module, _, attr = name.rpartition(".")
            file = self.by_module.get(module)
            if file is None:
                return name
            nxt = self.bound_names(file).get(attr)
            if nxt is None or nxt == name:
                return name
            name = nxt
        return name

    # --- classes

    @cached_property
    def classes(self) -> dict[str, tuple[SourceFile, ast.ClassDef]]:
        """Every top-level class of the project by its full name, `<module>.<Class>`."""
        out: dict[str, tuple[SourceFile, ast.ClassDef]] = {}
        for file, tree in self.trees():
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    out.setdefault(f"{file.module}.{node.name}", (file, node))
        return out

    @cached_property
    def bases(self) -> dict[str, set[str]]:
        """Each class's direct bases by full name; a base that does not resolve is left out."""
        return {
            name: {b for b in (self.full_name(file, base) for base in cls.bases) if b}
            for name, (file, cls) in self.classes.items()
        }

    def subclasses(self, base: str) -> set[str]:
        """Every class of the project that derives from `base`, at any depth; `base` itself left out."""
        found: set[str] = set()
        frontier = {base}
        while frontier:
            frontier = {name for name, bases in self.bases.items() if bases & frontier} - found
            found |= frontier
        return found

    def defined(self, module_prefix: str, class_name: str) -> list[str]:
        """The full names of the classes called `class_name` under `module_prefix`."""
        return [
            n
            for n in self.classes
            if n.rpartition(".")[2] == class_name and is_under(n.rpartition(".")[0], module_prefix)
        ]


# --- ast helpers


def is_under(name: str, prefix: str) -> bool:
    """Whether module `name` is `prefix` or inside it.

    `acme.om.x` is under `acme.om`, and `acme.omx` is not.
    """
    return name == prefix or name.startswith(prefix + ".")


def dotted(node: ast.AST | None) -> str | None:
    """The dotted name an expression spells (`a.b.C`), a subscript or a call unwrapped; else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = dotted(node.value)
        return f"{head}.{node.attr}" if head else None
    if isinstance(node, ast.Subscript | ast.Call):
        return dotted(node.func if isinstance(node, ast.Call) else node.value)
    return None


def last(name: str | None) -> str | None:
    """The last segment of a dotted name: `last("a.b.C")` is `C`."""
    return name.rpartition(".")[2] if name else None


def functions(node: ast.AST) -> list[Function]:
    """Every function or method defined anywhere under `node`, in source order."""
    found = [n for n in ast.walk(node) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    return sorted(found, key=lambda n: (n.lineno, n.col_offset))


def docstrings(tree: ast.AST) -> set[int]:
    """The ids of every docstring node under `tree`: a module's, a class's, or a function's first string."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and node.body
        ):
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                out.add(id(first.value))
    return out


def string(node: ast.AST | None) -> str | None:
    """The value of a string literal, else None."""
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def names_in(node: ast.AST | None) -> list[str]:
    """The last segment of every name an expression mentions, an annotation's included, in order."""
    if node is None:
        return []
    out: list[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.append(n.id)
        elif isinstance(n, ast.Attribute):
            out.append(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            # a string annotation, `"SecretStr | None"`, is read as the expression it spells
            with contextlib.suppress(SyntaxError):
                out.extend(names_in(ast.parse(n.value, mode="eval")))
    return out
