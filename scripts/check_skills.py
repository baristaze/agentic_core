#!/usr/bin/env python3
"""Check every skill under skills/ and the scaffold's skills for a uniform shape.

A folder that does not exist holds no skill, and passes. Rules:
- every skills/<name>/SKILL.md has YAML frontmatter with `name` equal to the
  folder name, `agentic-` then lowercase words joined by one hyphen each,
  so it never collides with the guideline's `arch-*`, and a non-empty
  `description` of at most 500 characters, written as one double-quoted
  string; the descriptions together stay under 6000 characters. Each
  description is listed in a budget the host shares across every
  installed skill. The host truncates one entry at 1,536 characters, and
  when the listing overflows it drops the descriptions of the least-used
  skills. So this plugin keeps its share small;
- the frontmatter holds only the fields of the Agent Skills standard
  (https://agentskills.io/specification: `name`, `description`,
  `license`, `compatibility`, `metadata`, `allowed-tools`), plus
  `disable-model-invocation`, Claude Code's key, which VS Code, Cursor,
  and Factory also read: a person starts the skill by name, and the
  model never does. The standard's validator refuses that key, so only a
  skill that must never start on its own carries it. Any other key is an
  error: a misspelled key, `allowed_tools` for one, would otherwise be a
  skill that silently runs with no tool limits. A `compatibility` holds
  at most 500 characters, as the standard says;
- a skill that says `disable-model-invocation: true` carries Codex's
  switch too, `agents/openai.yaml` in its folder with
  `policy.allow_implicit_invocation: false`, and a skill whose
  `agents/openai.yaml` turns implicit invocation off says the key: in
  both agents a person starts it, never the model;
- a skill names its own files by a path from its own folder, as the
  standard says, and never through a substitution one agent makes
  (`${CLAUDE_SKILL_DIR}`, `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PROJECT_DIR}`):
  an agent without it reads a wrong path. Nor does a skill or an agent
  file name its arguments as `$ARGUMENTS`, for the same reason. A path
  from the folder is one that climbs out of it (`../`) or one under its
  `references/`; every such path in a skill's Markdown resolves to a
  file or directory that exists inside this repository. A plugin skill
  that names a path climbing out of the folder (`../`) says to read it
  as `realpath` resolves the folder: a skill linked into a project is
  read through the link, and an agent that shortens `../` from the
  link's own path reads a file that is not there;
- allowed-tools is comma-separated, each entry `Name` or `Name(rule)`, with no
  space outside the parentheses (a space inside, `Bash(make check)`, is
  part of the rule); a bare
  `Bash` is refused, as is a rule with a trailing space inside the
  parentheses or the `Bash(cmd *)` spelling; a Bash rule is the
  `Bash(cmd:*)` prefix form, with no other `*`, or an exact
  `Bash(make <target>)`, and anything else (`Bash(*)`, `Bash(curl*)`, an
  exact command that is not make) is refused, as is a rule with a shell
  operator (`;`, `&`, `|`, a redirect, a substitution, a quote) that would
  chain a second command; a make entry names a target, so `Bash(make:*)`
  and `Bash(make -C dir:*)` are refused;
- every skill, the scaffold's included, has a non-empty allowed-tools:
  a skill without one runs with every tool the session has;
- allowed-tools names only what the body runs; the checker holds the make
  targets to it: for every `Bash(make <target>)` or `Bash(make <target>:*)`,
  `make <target>`, as whole words, appears inside a backticked span of the
  skill body, fenced code left out. Git, uv, and pnpm entries are checked
  by hand;
- frontmatter is flat `key: value` lines, one per key, no key repeated,
  and a space follows each key's colon (`name:foo` is one string to YAML);
- a double-quoted value is one complete YAML double-quoted scalar: it
  closes, its inner quotes are escaped, its escapes are ones YAML defines,
  and nothing but a comment follows the closing quote;
- an unquoted value contains no ": " or " #", and does not start with a
  YAML indicator character, so strict YAML loaders accept it;
- every skill of the scaffold, `scaffold/acme_root/.agents/skills/<name>/SKILL.md`,
  which a render of the scaffold runs as a real skill, has the frontmatter
  a skill has: only the standard's keys and `disable-model-invocation`, a
  name that is its folder's and the standard's (lowercase words joined by
  one hyphen each, at most 64 characters), a description of at most 1024
  characters in one double-quoted string, and allowed-tools entries each a
  Name or a `Bash(cmd:*)` prefix, comma-separated. Every path it names
  from its folder resolves inside `scaffold/acme_root/`, the tree a render
  carries. `.agents/skills/` is the folder every agent that reads the
  standard shares, and `scaffold/acme_root/.claude/skills` is a link to
  it, `../.agents/skills`, for Claude Code, which reads only its own.

Exit status is non-zero on any failure. Standard library only.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from _common import arguments, fenced_lines

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
AGENTS = ROOT / "agents"
NAME = re.compile(r"^agentic-[a-z0-9]+(?:-[a-z0-9]+)*$")
STANDARD_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
"""The standard's name: lowercase letters and digits, one hyphen between words, none at either end."""
NAME_LIMIT = 64
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
REF = re.compile(r"(?<![\w./-])((?:\.\./)+[\w.-][^\s`'\")]*|references/[^\s`'\")]+)")
"""A path from a skill's own folder: one that climbs out of it (`../`), or one under its `references/`.

Any other path a skill names is the tree's, read from its root.
"""
SUBSTITUTION = re.compile(r"\$\{CLAUDE_(?:SKILL_DIR|PLUGIN_ROOT|PLUGIN_DATA|PROJECT_DIR)\}|\$ARGUMENTS\b")
"""A path or the arguments, as one agent substitutes them and the others read them: as text."""
TOOL = re.compile(r"^(?:[A-Za-z]+|mcp__[A-Za-z0-9_-]+__[A-Za-z0-9_-]+)(\([^()]*\))?$")
"""A tool: a Name, or an MCP tool, `mcp__<server>__<tool>`, whose server a connector may name in any case."""
BASH_RULE = re.compile(r"^Bash\((.*)\)$")
# A rule names one command: no shell operator (`;`, `&`, `|`, a redirect, a
# substitution, a quote) may chain a second one behind the first.
PREFIX_RULE = re.compile(r"^[^*:;&|<>`$()'\"\\\n]+:\*$")  # `cmd:*`: a command, then the one `*`
EXACT_MAKE = re.compile(r"^make [^*:;&|<>`$()'\"\\\n]+$")  # `make <target>`, arguments allowed, no wildcard
MAKE_TARGET = re.compile(r"^make [^\s-]")  # a make entry names a target first, not an option
QUOTED_DESCRIPTION = re.compile(r'^description:\s*"', re.M)
PARENS = re.compile(r"\([^()]*\)")
CODE_SPAN = re.compile(r"`([^`\n]+)`")
DESCRIPTION_LIMIT = 500
DESCRIPTIONS_TOTAL = 6000
STANDARD_KEYS = frozenset({"name", "description", "license", "compatibility", "metadata", "allowed-tools"})
"""The frontmatter fields of the Agent Skills standard."""
KNOWN_KEYS = STANDARD_KEYS | {"disable-model-invocation"}
"""The standard's fields, and Claude Code's key that keeps the model from starting a skill."""
OPENAI_SETTINGS = "agents/openai.yaml"
"""Codex's file of a skill's own settings, in the skill's folder."""
IMPLICIT_OFF = re.compile(r"^[ \t]+allow_implicit_invocation:[ \t]*false[ \t]*(?:#.*)?$")
REALPATH = "`realpath`"
NO_REALPATH = "names a path out of its folder (../) and never says to read it as `realpath` resolves the folder"
"""How a skill that climbs out of its folder says to resolve the folder first, when it is a link."""
COMPATIBILITY_LIMIT = 500
DESCRIPTION_STANDARD_LIMIT = 1024
KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
ESCAPES = '0abtnvfre "/\\N_LP\t'  # single-character escapes YAML defines after a backslash
HEX_ESCAPES = {"x": 2, "u": 4, "U": 8}
INDICATORS = ("'", "[", "{", "&", "*", "!", "|", ">", "%", "@", "`", "#", "-", "?", ",", "]", "}")
NO_TOOLS = "no allowed-tools; a skill names the tools it runs"
"""A skill with no allowed-tools runs with every tool the session has, so the key is required."""

SCAFFOLD_ROOT = ROOT / "scaffold" / "acme_root"
"""The tree a render of the scaffold carries: a scaffold skill's paths stay inside it."""
SCAFFOLD_SKILLS = ROOT / "scaffold" / "acme_root" / ".agents" / "skills"
"""The skills of the scaffold: a render carries them with the rest, and runs them as its own."""
SCAFFOLD_LINK = ROOT / "scaffold" / "acme_root" / ".claude" / "skills"
"""Claude Code reads only `.claude/skills/`, so the scaffold's is a link to its `.agents/skills/`."""
LINK_TARGET = "../.agents/skills"


def double_quoted(value: str) -> tuple[str, str | None]:
    """Parse one YAML double-quoted scalar; return (content, error).

    `value` starts with the opening quote. The content is returned raw,
    escapes kept, because the checks only need its length and text.
    """
    i = 1
    while i < len(value):
        ch = value[i]
        if ch == '"':
            rest = value[i + 1 :].strip()
            if rest and not rest.startswith("#"):
                return value[1:i], f"text after the closing quote: {rest!r}"
            return value[1:i], None
        if ch == "\\":
            esc = value[i + 1 : i + 2]
            if esc in HEX_ESCAPES:
                digits = value[i + 2 : i + 2 + HEX_ESCAPES[esc]]
                if len(digits) != HEX_ESCAPES[esc] or not all(c in "0123456789abcdefABCDEF" for c in digits):
                    return value[1:], f"bad escape \\{esc}{digits}"
                i += 2 + HEX_ESCAPES[esc]
                continue
            if not esc or esc not in ESCAPES:
                return value[1:], f"bad escape \\{esc}"
            i += 2
            continue
        i += 1
    return value[1:], "unterminated quoted value"


def frontmatter(text: str, errors: list[str], rel: str) -> dict[str, str]:
    """Parse the flat `key: value` frontmatter strictly enough for any YAML loader.

    A value is either one complete double-quoted scalar on the key's line
    or a plain scalar that no YAML rule would read differently: no ": ",
    no " #", no leading indicator. Anything else is refused here so a
    strict loader elsewhere never sees it first.
    """
    m = FRONTMATTER.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        if line[0] in " \t":
            errors.append(f"{rel}: frontmatter line is indented; values are one line each: {line!r}")
            continue
        key, sep, value = line.partition(":")
        # YAML reads `name:foo` as one plain string, never a key: a colon ends a key only before a space
        if not sep or not key.strip() or not KEY.match(key.strip()) or (value and value[0] not in " \t"):
            errors.append(f"{rel}: frontmatter line is not `key: value`: {line!r}")
            continue
        key, value = key.strip(), value.strip()
        if key in out:
            errors.append(f"{rel}: frontmatter key {key!r} repeated")
        if value.startswith('"'):
            value, problem = double_quoted(value)
            if problem:
                errors.append(f"{rel}: value of {key} is not one double-quoted string: {problem}")
        elif ": " in value or " #" in value or value.endswith(":") or value[:1] in INDICATORS:
            errors.append(f"{rel}: value of {key} must be double-quoted for strict YAML")
        out[key] = value
    return out


def body_of(text: str) -> str:
    """The skill text after the frontmatter."""
    m = FRONTMATTER.match(text)
    return text[m.end() :] if m else text


def references(text: str) -> list[str]:
    """Every path from a skill's folder that `text` names, a sentence's closing punctuation left off (`../..` keeps its dots)."""
    return [re.sub(r"(?<!\.)[.,;:]+$", "", ref) for ref in REF.findall(text)]


def check_keys(fm: dict[str, str], rel: str, errors: list[str]) -> None:
    """The frontmatter holds only KNOWN_KEYS, and a `compatibility` within the standard's limit."""
    for key in sorted(set(fm) - KNOWN_KEYS):
        errors.append(
            f"{rel}: frontmatter key {key!r} is neither a field of the Agent Skills standard nor "
            f"disable-model-invocation ({', '.join(sorted(KNOWN_KEYS))})"
        )
    size = len(fm.get("compatibility", "x"))
    if not 0 < size <= COMPATIBILITY_LIMIT:
        errors.append(f"{rel}: compatibility is {size} characters; the standard allows 1 to {COMPATIBILITY_LIMIT}")


def implicit_off(settings: Path) -> bool:
    """Whether Codex's `agents/openai.yaml` sets `allow_implicit_invocation: false` under `policy:`."""
    if not settings.is_file():
        return False
    inside = False
    for line in settings.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line[0].isspace():
            inside = line.split("#", 1)[0].strip() == "policy:"
        elif inside and IMPLICIT_OFF.match(line):
            return True
    return False


def check_invocation(folder: Path, fm: dict[str, str], rel: str, errors: list[str]) -> None:
    """Claude Code's `disable-model-invocation: true` and Codex's `policy.allow_implicit_invocation: false` agree."""
    manual = fm.get("disable-model-invocation", "").strip().lower() == "true"
    settings = folder / OPENAI_SETTINGS
    off = implicit_off(settings)
    where = settings.relative_to(ROOT).as_posix()
    if manual and not off:
        errors.append(
            f"{rel}: disable-model-invocation is true, but {where} does not set "
            "policy.allow_implicit_invocation: false, so Codex's model may start the skill"
        )
    elif off and not manual:
        errors.append(f"{rel}: {where} turns implicit invocation off; say disable-model-invocation: true too")


def check_reference(folder: Path, ref: str, within: Path, where: str, errors: list[str]) -> None:
    """A path from `folder` resolves to something that exists, inside `within`; a placeholder such as `<path>` is left alone."""
    if "<" in ref:
        return
    target = (folder / ref).resolve()
    if not target.is_relative_to(within.resolve()):
        place = "the repository" if within == ROOT else within.relative_to(ROOT).as_posix()
        errors.append(f"{where}: reference {ref} resolves outside {place}")
    elif not target.exists():
        errors.append(f"{where}: reference {ref} does not exist")


def code_spans(text: str) -> list[str]:
    """Every inline backticked span of a Markdown text, fences left out.

    A fenced block in a skill is a report template, never a command, so
    only inline spans count as the body running something.
    """
    out: list[str] = []
    for line, code in zip(text.split("\n"), fenced_lines(text), strict=True):
        if not code:
            out.extend(CODE_SPAN.findall(line))
    return out


def bash_command(tool: str) -> str | None:
    """The command a `Bash(cmd)` or `Bash(cmd:*)` entry names, or None.

    Only a make target is held to the body; `git`, `uv run`, and `pnpm
    run` entries are left to the reader.
    """
    m = BASH_RULE.match(tool)
    if not m:
        return None
    return m.group(1).removesuffix(":*").strip()


def runs_command(cmd: str, spans: list[str]) -> bool:
    """Whether a span runs `cmd` as whole words: `make test` is not `make test-e2e`."""
    word = re.compile(rf"(?<![\w-]){re.escape(cmd)}(?![\w-])")
    return any(word.search(span) for span in spans)


def check_tools(tools: str, spans: list[str], rel: str, errors: list[str]) -> None:
    """A plugin skill's allowed-tools: present, comma-separated, each entry a Name or a rule in an allowed form,
    and every make target it names run by the body."""
    if not tools.strip().strip('"').strip():
        errors.append(f"{rel}: {NO_TOOLS}")
        return
    if any(" " in PARENS.sub("", t.strip()) for t in tools.split(",")):
        errors.append(f"{rel}: allowed-tools must be comma-separated")
    for tool in (t.strip() for t in tools.split(",")):
        if not TOOL.match(tool):
            errors.append(f"{rel}: allowed-tools entry {tool!r} is not Name or Name(rule)")
            continue
        if tool == "Bash":
            errors.append(f"{rel}: a bare Bash is refused; name the command, Bash(cmd:*)")
            continue
        cmd = bash_command(tool)
        if cmd is None:
            continue
        rule = tool[len("Bash(") : -1]
        if rule != rule.strip():
            errors.append(f"{rel}: trailing space inside the parentheses of {tool!r}")
        if " *" in rule:
            errors.append(f"{rel}: use the Bash(cmd:*) prefix form, not {tool!r}")
        elif not cmd:
            errors.append(f"{rel}: allowed-tools entry {tool!r} names no command")
        elif not (PREFIX_RULE.match(rule.strip()) or EXACT_MAKE.match(rule.strip())):
            errors.append(f"{rel}: {tool!r} is neither the Bash(cmd:*) prefix form nor an exact Bash(make <target>)")
        elif (cmd == "make" or cmd.startswith("make ")) and not MAKE_TARGET.match(cmd):
            errors.append(f"{rel}: {tool!r} names no make target; name one, Bash(make <target>)")
        elif cmd.startswith("make ") and not runs_command(cmd, spans):
            errors.append(f"{rel}: allowed-tools names Bash({cmd}) but the body never runs {cmd}")


def check_skill(folder: Path, errors: list[str]) -> int:
    """One plugin skill: its frontmatter, its tools, and every path it names from its folder. Returns its
    description's length, for the shared budget."""
    skill = folder / "SKILL.md"
    rel = str(skill.relative_to(ROOT))
    if not skill.exists():
        errors.append(f"{folder.relative_to(ROOT)}: no SKILL.md")
        return 0
    text = skill.read_text(encoding="utf-8")
    fm = frontmatter(text, errors, rel)
    if not fm:
        errors.append(f"{rel}: missing frontmatter")
        return 0
    name = fm.get("name", "")
    if name != folder.name:
        errors.append(f"{rel}: name '{name}' differs from folder '{folder.name}'")
    if not NAME.match(name) or len(name) > NAME_LIMIT:
        errors.append(f"{rel}: name '{name}' must match {NAME.pattern}, at most {NAME_LIMIT} characters")
    check_keys(fm, rel, errors)
    check_invocation(folder, fm, rel, errors)
    desc = fm.get("description", "")
    if not desc:
        errors.append(f"{rel}: empty description")
    elif len(desc) > DESCRIPTION_LIMIT:
        errors.append(f"{rel}: description is {len(desc)} characters, limit {DESCRIPTION_LIMIT}")
    head = FRONTMATTER.match(text)
    if desc and head and not QUOTED_DESCRIPTION.search(head.group(1)):
        errors.append(f"{rel}: description must be one double-quoted string")
    body = body_of(text)
    check_tools(fm.get("allowed-tools", ""), code_spans(body), rel, errors)
    for file in sorted(folder.rglob("*.md")):
        where = str(file.relative_to(ROOT))
        for ref in references(body_of(file.read_text(encoding="utf-8"))):
            check_reference(folder, ref, ROOT, where, errors)
    if any(ref.startswith("../") for ref in references(body)) and REALPATH not in body:
        errors.append(f"{rel}: {NO_REALPATH}")
    return len(desc)


def scaffold_skills() -> list[Path]:
    """The `SKILL.md` of every skill of the scaffold, by folder name; a folder whose name starts with `_` is shared text."""
    if not SCAFFOLD_SKILLS.is_dir():
        return []
    return sorted(p / "SKILL.md" for p in SCAFFOLD_SKILLS.iterdir() if p.is_dir() and not p.name.startswith("_"))


def check_scaffold_skill(path: Path, errors: list[str]) -> None:
    """A scaffold skill's frontmatter holds to the rules a skill's does: the standard's keys, a name that is its
    folder's and the standard's, the description one double-quoted string, and every allowed-tools entry a Name or
    a Bash(cmd:*) prefix. Every path it names from its folder resolves inside the tree a render carries."""
    rel = path.relative_to(ROOT)
    if not path.exists():
        errors.append(f"{rel.parent}: no SKILL.md")
        return
    text = path.read_text(encoding="utf-8")
    fm = frontmatter(text, errors, str(rel))
    if not fm:
        errors.append(f"{rel}: missing frontmatter")
        return
    check_keys(fm, str(rel), errors)
    check_invocation(path.parent, fm, str(rel), errors)
    name = fm.get("name", "")
    if name != path.parent.name:
        errors.append(f"{rel}: name '{name}' differs from its folder '{path.parent.name}'")
    if not STANDARD_NAME.match(name) or len(name) > NAME_LIMIT:
        errors.append(f"{rel}: name '{name}' is not lowercase words joined by one hyphen each, at most {NAME_LIMIT} characters")
    for file in sorted(path.parent.rglob("*.md")):
        where = str(file.relative_to(ROOT))
        for ref in references(body_of(file.read_text(encoding="utf-8"))):
            check_reference(path.parent, ref, SCAFFOLD_ROOT, where, errors)
    desc = fm.get("description", "")
    head = FRONTMATTER.match(text)
    if not desc:
        errors.append(f"{rel}: empty description")
    elif len(desc) > DESCRIPTION_STANDARD_LIMIT:
        errors.append(f"{rel}: description is {len(desc)} characters, limit {DESCRIPTION_STANDARD_LIMIT}")
    elif head and not QUOTED_DESCRIPTION.search(head.group(1)):
        errors.append(f"{rel}: description must be one double-quoted string")
    tools = fm.get("allowed-tools", "")
    if not tools.strip().strip('"').strip():
        errors.append(f"{rel}: {NO_TOOLS}")
    if any(" " in PARENS.sub("", t.strip()) for t in tools.split(",")):
        errors.append(f"{rel}: allowed-tools must be comma-separated")
    for tool in (t.strip() for t in tools.split(",") if t.strip()):
        if not TOOL.match(tool):
            errors.append(f"{rel}: allowed-tools entry {tool!r} is not Name or Name(rule)")
        elif tool == "Bash":
            errors.append(f"{rel}: a bare Bash is refused; name the command, Bash(cmd:*)")
        elif bash_command(tool) is not None:
            rule = tool[len("Bash(") : -1]
            if " *" in rule or not (PREFIX_RULE.match(rule.strip()) or EXACT_MAKE.match(rule.strip())):
                errors.append(f"{rel}: {tool!r} is neither the Bash(cmd:*) prefix form nor an exact Bash(make <target>)")


def check_scaffold_link(errors: list[str]) -> None:
    """The scaffold's `.claude/skills` is a link to LINK_TARGET, so Claude Code and every other agent find the same
    files; a render carries the link."""
    if not SCAFFOLD_SKILLS.is_dir():
        return
    rel = SCAFFOLD_LINK.relative_to(ROOT)
    if not SCAFFOLD_LINK.is_symlink():
        errors.append(f"{rel}: not a link; Claude Code reads the scaffold's skills through a link to {LINK_TARGET}")
    elif os.readlink(SCAFFOLD_LINK) != LINK_TARGET:
        errors.append(f"{rel}: links to {os.readlink(SCAFFOLD_LINK)!r}; it links to {LINK_TARGET!r}")


def check_substitutions(errors: list[str]) -> None:
    """No skill, reference file, or agent file names a path, or its arguments, through a substitution one agent
    makes."""
    files = [*(SKILLS.rglob("*.md") if SKILLS.is_dir() else []), *(AGENTS.glob("*.md") if AGENTS.is_dir() else [])]
    files += SCAFFOLD_SKILLS.rglob("*.md") if SCAFFOLD_SKILLS.is_dir() else []
    for path in sorted(files):
        for found in sorted(set(SUBSTITUTION.findall(path.read_text(encoding="utf-8")))):
            errors.append(
                f"{path.relative_to(ROOT)}: names {found}, a substitution one agent makes and the others read as text; "
                "name a file by its path from the skill's folder, and the arguments as the arguments"
            )


def main(argv: Sequence[str] = ()) -> int:
    arguments(__doc__, argv)
    errors: list[str] = []
    skills = sorted(p for p in SKILLS.iterdir() if p.is_dir() and not p.name.startswith("_")) if SKILLS.is_dir() else []
    total = sum(check_skill(folder, errors) for folder in skills)
    if total > DESCRIPTIONS_TOTAL:
        errors.append(f"skills/: the descriptions total {total} characters, limit {DESCRIPTIONS_TOTAL}")
    copied = scaffold_skills()
    for skill in copied:
        check_scaffold_skill(skill, errors)
    check_scaffold_link(errors)
    check_substitutions(errors)
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} problem(s) in {len(skills)} skill(s) and {len(copied)} scaffold skill(s)")
        return 1
    print(f"skills ok: {len(skills)} skills, {len(copied)} scaffold skills")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
