#!/usr/bin/env python3
"""Check that every lens file follows the format in lenses/README.md and cites a real section of the spec.

A lens holds the checkable detail under a rule `agentic_core_spec.md`
states. It is stricter than the spec, never contrary to it. This check
holds its format, its citations, and the names it quotes; that it stays
inside its rule is held by review.

Rules:
- every group listed in lenses/README.md has a file, and every file is listed;
  each group lists its own prefix, and no two groups share one;
- lens headings are `## <PREFIX>-NN Title`, with the prefix the group lists,
  numbered 01.. in order;
- every lens has Principle, Source, Look for, Violation, Severity, an
  optional Shape, and Check, in that order;
- Source names sections of the spec by title, never by number: `<Section>`
  or `<Section>, <Subsection>`, several separated by `;`, where a bare
  `<Subsection>` after a `;` belongs to the section cited before it. A
  title may hold commas of its own (`Loops, Runs, and Sessions`), so a
  citation is read as a whole section first, then as each split at `, `
  into a section and one of its subsections. A citation of a subsection
  may end in `(<Label>, <Label>)`, and each label is a bold paragraph
  label `**<Label>.**` inside that subsection; a citation of a whole
  section takes no parentheses;
- Severity is high, medium, or low;
- a Principle is at most 120 words, and Look for and Violation are at most
  three sentences each, so a lens stays one rule a reviewer can hold;
- a field value runs to the next field or lens heading, wrapped lines
  and list items included; fenced code is neither a lens nor a field,
  so a lens file can show lens syntax in an example;
- no line of a lens file is wider than 80 columns;
- a lens count stated in README.md or lenses/README.md ("N lenses") equals
  the size of the catalog;
- an optional Shape field, after Severity, names one or two files or
  folders of the scaffold that show the rule, each a path in backticks
  that starts `scaffold/acme_root/` and exists, separated by a comma or
  `and`;
- the Check field reads `review` when the review alone judges the lens,
  "`agentic-check` decides it." when the checker decides it whole, or
  "`agentic-check` decides <part>; the rest is judged." when it decides a
  part. It agrees both ways with the rules the scaffold's checker
  registers, in `scaffold/acme_root/checkers/src/acme/agentic_check/rules/`:
  a lens that names the checker has a rule of its id with the same
  coverage (`full` or `partial`), and every rule decides a lens that
  names it. The rules are read with `ast`, never imported;
- an identifier a lens quotes stays in the section it cites. A section is
  the text under its `##` heading, its subsections, code, and agents-only
  blocks (`<!-- agents-only ... -->`) included, and no other HTML comment.
  An identifier is a backticked name written as code: it holds an
  underscore, a lower-case letter before a capital, a dot, or a closing
  `()` (`responds_to`, `AgentSession`, `ctx.deadline`, `is_input()`); a
  file name is not one. Every identifier in a Principle is held to the
  cited sections, because the Principle restates them. An identifier in
  Look for or Violation is held to them when the spec names it anywhere;
  one it never names is the lens's own example of a breach;
- a tag is one of `core`, `default`, `optional`, and `style`, in inline
  code, alone on the first line under a `##` or `###` heading. A lone
  backticked word there that is none of the four is refused. A tag covers
  its own heading's text, not the headings below it;
- a lens whose every citation names a section or subsection tagged
  `style` is `low`: a house convention's breach is a low finding at most.

Exit status is non-zero when any rule fails. Standard library only.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "agentic_core_spec.md"
LENSES = ROOT / "lenses"
README = ROOT / "README.md"
RULES = ROOT / "scaffold" / "acme_root" / "checkers" / "src" / "acme" / "agentic_check" / "rules"

FIELDS = ("Principle", "Source", "Look for", "Violation", "Severity")
SHAPE = "Shape"
CHECK = "Check"
ORDERS = ([*FIELDS, CHECK], [*FIELDS, SHAPE, CHECK])
"""The fields of a lens, in order: the five, then Shape when the lens has one, then Check."""
CHECK_REVIEW = "review"
CHECKER = "agentic-check"
CHECK_FULL = f"`{CHECKER}` decides it."
CHECK_PARTIAL = re.compile(rf"^`{CHECKER}` decides (.+); the rest is judged\.$")
SEVERITIES = {"high", "medium", "low"}
HEADING = re.compile(r"^## ([A-Z]{2,3})-(\d{2}) (.+)$")
FIELD = re.compile(r"^\*\*(Principle|Source|Look for|Violation|Severity|Shape|Check)\.\*\*\s*(.*)$")
SHAPE_ROOT = "scaffold/acme_root/"
MAX_SHAPES = 2
LIST_MARKER = re.compile(r"^(?:[-*+]|\d+\.)\s+")
NUMBERED = re.compile(r"\b(?:sub)?sections?\s+\d+|§\s*\d+", re.IGNORECASE)
"""A reference to a section by number: `section 4`, `subsection 3.2`, `§4`."""
TABLE_ROW = re.compile(r"^\|\s*`([a-z]+)`\s*\|\s*`([A-Z]{2,3})`\s*\|\s*`([a-z]+\.md)`\s*\|")
SKIP_SECTIONS = {"Contents"}
LABELLED = re.compile(r"^(.*?)\s*\(([^()]*)\)$")
BOLD_LABEL = re.compile(r"\*\*([^*]+?)\.\*\*")
COUNT = re.compile(r"\b(\d+) lenses\b")
SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
IDENTIFIER = re.compile(r"(?=.*(?:_|[a-z][A-Z]|\.|\(\)$))[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:\(\))?")
FILE_NAME = re.compile(r".+\.(?:py|pyi|toml|json|jsonc|html|md|txt|ya?ml|sql|ts|tsx|js|mjs|lock|cfg|ini|sh|env)")
CODE_SPAN = re.compile(r"`([^`]+)`")
TAGS = ("core", "default", "optional", "style")
TAG_LINE = re.compile(r"^`([a-z]+)`$")
HEADING_LINE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
CLOSING = re.compile(r"(?:^|\s+)#+$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
AGENTS_ONLY = re.compile(r"^\s*<!--\s*agents-only\s*$")
MAX_PRINCIPLE_WORDS = 120
MAX_SENTENCES = 3
MAX_COLUMNS = 80

Part = tuple[str, str | None]
"""What one citation names: (section, None) for a whole section, (section, subsection) for a part."""


def fenced_lines(text: str) -> list[bool]:
    """For each line of `text`, whether it is fenced code, its fences included, as CommonMark reads a fence."""
    out: list[bool] = []
    opener: str | None = None
    for line in text.split("\n"):
        m = FENCE.match(line)
        if opener is None and m:
            opener = m.group(1)
            out.append(True)
        elif opener is not None:
            if m and m.group(1)[0] == opener[0] and len(m.group(1)) >= len(opener) and not line.strip().strip(opener[0]):
                opener = None
            out.append(True)
        else:
            out.append(False)
    return out


def unfenced(text: str) -> str:
    """The text with every line of fenced code blanked to spaces, so line numbers still map back to the file."""
    lines = text.split("\n")
    return "\n".join(" " * len(line) if code else line for line, code in zip(lines, fenced_lines(text), strict=True))


def commented_lines(text: str) -> list[str | None]:
    """For each line, the kind of HTML comment it lies in: "agents-only", "comment", or None.

    A comment opens on a line outside fenced code that starts with `<!--`
    and runs to the first line holding `-->`, both included.
    """
    out: list[str | None] = []
    kind: str | None = None
    for line, code in zip(text.split("\n"), fenced_lines(text), strict=True):
        if kind is None and not code and line.lstrip().startswith("<!--"):
            kind = "agents-only" if AGENTS_ONLY.match(line) else "comment"
            out.append(kind)
            if "-->" in line.split("<!--", 1)[1]:
                kind = None
        elif kind is not None:
            out.append(kind)
            if "-->" in line:
                kind = None
        else:
            out.append(None)
    return out


def headings(text: str) -> list[tuple[int, str]]:
    """(level, title) for every ATX heading, skipping fenced code and HTML comments."""
    out: list[tuple[int, str]] = []
    for line, comment in zip(unfenced(text).split("\n"), commented_lines(text), strict=True):
        m = None if comment else HEADING_LINE.match(line)
        if m:
            out.append((len(m.group(1)), CLOSING.sub("", m.group(2))))
    return out


def sections() -> dict[str, set[str]]:
    """Map each section title of the spec to the set of its subsection titles."""
    out: dict[str, set[str]] = {}
    current: str | None = None
    for level, title in headings(SPEC.read_text(encoding="utf-8")):
        if level == 2:
            current = None if title in SKIP_SECTIONS else title
            if current is not None:
                out[current] = set()
        elif level == 3 and current is not None:
            out[current].add(title)
    return out


def paragraph_labels() -> dict[tuple[str, str], set[str]]:
    """Map each (section, subsection) to the bold paragraph labels in its text, outside code and comments."""
    out: dict[tuple[str, str], set[str]] = {}
    section: str | None = None
    current: tuple[str, str] | None = None
    text = SPEC.read_text(encoding="utf-8")
    for line, comment in zip(unfenced(text).split("\n"), commented_lines(text), strict=True):
        if comment:
            continue
        m = re.match(r"^(#{1,3}) (.+)$", line)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            section = title if level == 2 else section if level == 3 else None
            current = (section, title) if level == 3 and section is not None else None
            if current is not None:
                out.setdefault(current, set())
            continue
        if current is not None:
            out[current].update(BOLD_LABEL.findall(line))
    return out


def spec_text() -> str:
    """The spec as an agent reads it: every line but those of an HTML comment that is not an agents-only block."""
    text = SPEC.read_text(encoding="utf-8")
    kept = (line for line, comment in zip(text.split("\n"), commented_lines(text), strict=True) if comment != "comment")
    return "\n".join(kept)


def section_texts() -> dict[str, str]:
    """Map each section title to its text: from its `##` heading to the next, subsections and agents-only blocks included."""
    text = spec_text()
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line, code in zip(text.split("\n"), fenced_lines(text), strict=True):
        m = None if code else re.match(r"^## (.+?)\s*$", line)
        if m:
            current = m.group(1)
            out[current] = []
        elif current is not None:
            out[current].append(line)
    return {title: "\n".join(lines) for title, lines in out.items()}


def tags(errors: list[str] | None = None) -> dict[Part, str]:
    """Map (section, subsection or None) to the tag under its heading; a lone backticked word that is no tag is an error."""
    text = SPEC.read_text(encoding="utf-8")
    out: dict[Part, str] = {}
    section: str | None = None
    pending: Part | None = None
    lines = unfenced(text).split("\n")
    for n, (line, comment) in enumerate(zip(lines, commented_lines(text), strict=True), start=1):
        if comment:
            continue
        m = re.match(r"^(#{2,3}) (.+?)\s*$", line)
        if m:
            level, title = len(m.group(1)), m.group(2)
            section = title if level == 2 else section
            pending = (title, None) if level == 2 else (section, title) if section is not None else None
            continue
        if not line.strip():
            continue
        tag = TAG_LINE.match(line.strip())
        if pending is not None and tag:
            if tag.group(1) in TAGS:
                out[pending] = tag.group(1)
            elif errors is not None:
                errors.append(f"{SPEC.name}:{n}: `{tag.group(1)}` is no tag; a tag is one of {', '.join(TAGS)}")
        pending = None
    return out


def citations(value: str) -> list[str]:
    """The citations of a Source value, split at `;`, each trimmed of a closing period."""
    return [c.strip().rstrip(".") for c in value.split(";") if c.strip()]


def resolve(citation: str, previous: str | None, known: dict[str, set[str]]) -> Part | str:
    """What one citation names, or an error message when it names nothing in the spec.

    A whole section is tried first, then each split at `, ` into a section
    and one of its subsections, then a bare subsection of the section cited
    before it.
    """
    if citation in known:
        return (citation, None)
    heads: list[str] = []
    for m in re.finditer(", ", citation):
        head, tail = citation[: m.start()], citation[m.end() :]
        if head in known:
            if tail in known[head]:
                return (head, tail)
            heads.append(head)
    if previous is not None and citation in known[previous]:
        return (previous, citation)
    if heads:
        head = heads[-1]
        return f"'{head}' has no subsection '{citation[len(head) + 2 :]}'"
    if previous is not None:
        return f"'{citation}' is neither a section nor a subsection of '{previous}'"
    return f"'{citation}' is not a section of {SPEC.name}"


def cited_parts(value: str, known: dict[str, set[str]]) -> list[Part]:
    """What a Source value cites, in order; a citation that names nothing is left out."""
    out: list[Part] = []
    previous: str | None = None
    for citation in citations(value):
        m = LABELLED.match(citation)
        found = resolve(m.group(1).strip() if m else citation, previous, known)
        if isinstance(found, tuple):
            previous = found[0]
            out.append(found)
    return out


def names(text: str, identifier: str) -> bool:
    """Whether `text` names an identifier as a whole word; a call is named by its function (`is_input` for `is_input()`)."""
    name = identifier.removesuffix("()")
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) is not None


def check_identifiers(
    lens_id: str,
    fields: list[tuple[str, str, int]],
    path: Path,
    known: dict[str, set[str]],
    texts: dict[str, str],
    errors: list[str],
) -> None:
    """Every identifier the lens quotes is in a section it cites (see the module docstring)."""
    source = next((value for name, value, _ in fields if name == "Source"), "")
    cited = list(dict.fromkeys(section for section, _ in cited_parts(source, known)))
    if not cited:
        return  # an unknown citation is reported by check_source
    held = "\n".join(texts.get(c, "") for c in cited)
    everywhere = spec_text()
    for name, value, ln in fields:
        if name not in ("Principle", "Look for", "Violation"):
            continue
        for span in CODE_SPAN.findall(value):
            identifier = span.strip()
            if not IDENTIFIER.fullmatch(identifier) or FILE_NAME.fullmatch(identifier):
                continue
            if names(held, identifier):
                continue
            if name != "Principle" and not names(everywhere, identifier):
                continue  # the lens's own example of a breach, which the spec never names
            errors.append(f"{path.name}:{ln}: {lens_id} quotes `{identifier}`, which {' and '.join(cited)} does not hold")


def listed_groups(errors: list[str]) -> dict[str, tuple[str, str]]:
    """The groups lenses/README.md lists: group -> (prefix, file). A prefix two groups share is an error."""
    groups: dict[str, tuple[str, str]] = {}
    for line in (LENSES / "README.md").read_text(encoding="utf-8").splitlines():
        m = TABLE_ROW.match(line)
        if m:
            groups[m.group(1)] = (m.group(2), m.group(3))
    seen: dict[str, str] = {}
    for group, (prefix, _file) in groups.items():
        if prefix in seen:
            errors.append(f"lenses/README.md lists prefix {prefix} for both {seen[prefix]} and {group}")
        seen.setdefault(prefix, group)
    return groups


def check_source(
    value: str,
    path: Path,
    ln: int,
    known: dict[str, set[str]],
    errors: list[str],
    labels: dict[tuple[str, str], set[str]],
) -> None:
    """A source is one or more citations separated by ';', each naming a heading of the spec (see `resolve`)."""
    if NUMBERED.search(value):
        errors.append(f"{path.name}:{ln}: cites a section by number: '{value}'")
        return
    cites = citations(value)
    if not cites:
        errors.append(f"{path.name}:{ln}: empty source")
        return
    previous: str | None = None
    for citation in cites:
        wanted: list[str] | None = None
        m = LABELLED.match(citation)
        if m:
            citation, wanted = m.group(1).strip(), [label.strip() for label in m.group(2).split(",")]
        found = resolve(citation, previous, known)
        if isinstance(found, str):
            errors.append(f"{path.name}:{ln}: {found}")
            continue
        previous, sub = found
        if wanted is None:
            continue
        if sub is None:
            errors.append(f"{path.name}:{ln}: '{citation}' names no subsection, so it takes no labels in parentheses")
            continue
        present = labels.get((previous, sub), set())
        for label in wanted:
            if label not in present:
                errors.append(f"{path.name}:{ln}: '{previous}, {sub}' has no paragraph labelled '**{label}.**'")


def check_style(
    lens_id: str,
    fields: list[tuple[str, str, int]],
    path: Path,
    known: dict[str, set[str]],
    tagged: dict[Part, str],
    errors: list[str],
) -> None:
    """A lens whose every citation is tagged `style` is `low`: a house convention's breach is low at most."""
    source = next((value for name, value, _ in fields if name == "Source"), "")
    severity = next(((value.strip("` "), ln) for name, value, ln in fields if name == "Severity"), None)
    parts = cited_parts(source, known)
    if severity is None or severity[0] == "low" or not parts:
        return
    if all(tagged.get(part) == "style" for part in parts):
        errors.append(
            f"{path.name}:{severity[1]}: {lens_id} is {severity[0]}, and every section it cites is tagged `style`; "
            "a lens on a house convention is low"
        )


def check_shape(value: str, path: Path, ln: int, errors: list[str]) -> None:
    """A Shape names one or two paths of the scaffold, each in backticks and each there, and nothing else."""
    spans = CODE_SPAN.findall(value)
    rest = CODE_SPAN.sub("", value).strip(" ,.")
    if not 1 <= len(spans) <= MAX_SHAPES or rest not in ("", "and"):
        errors.append(f"{path.name}:{ln}: Shape reads '{value}'; it names one or two paths under {SHAPE_ROOT}, each in backticks")
        return
    for span in spans:
        target = (ROOT / span).resolve()
        if not span.startswith(SHAPE_ROOT) or not target.is_relative_to((ROOT / SHAPE_ROOT).resolve()):
            errors.append(f"{path.name}:{ln}: Shape names `{span}`, which is not under {SHAPE_ROOT}")
        elif not target.exists():
            errors.append(f"{path.name}:{ln}: Shape names `{span}`, which does not exist")


def lens_fields(lines: list[str], start: int, fenced: set[int]) -> tuple[list[tuple[str, str, int]], int]:
    """The fields of the lens whose heading is at `start`, and the index of the line after it.

    A wrapped line or a list item continues the field before it.
    """
    fields: list[tuple[str, str, int]] = []
    j = start + 1
    while j < len(lines) and (j in fenced or not HEADING.match(lines[j])):
        fm = None if j in fenced else FIELD.match(lines[j])
        if fm:
            fields.append((fm.group(1), fm.group(2).strip(), j + 1))
        elif fields and j not in fenced and lines[j].strip():
            name, value, ln = fields[-1]
            line = LIST_MARKER.sub("", lines[j].strip())
            fields[-1] = (name, f"{value} {line}".strip(), ln)
        j += 1
    return fields, j


def coverage_of(value: str) -> str | None:
    """What a Check line says the checker decides: `full`, `partial`, or None when it names no checker."""
    if value == CHECK_FULL:
        return "full"
    return "partial" if CHECK_PARTIAL.match(value) else None


def registered_rules() -> dict[str, tuple[str, str]]:
    """Every `@rule("<ID>", coverage=...)` under the checker's rules package: id -> (coverage, file)."""
    out: dict[str, tuple[str, str]] = {}
    if not RULES.is_dir():
        return out
    for path in sorted(RULES.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "rule"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                continue
            coverage = next(
                (k.value.value for k in node.keywords if k.arg == "coverage" and isinstance(k.value, ast.Constant)), None
            )
            out[node.args[0].value] = (str(coverage), path.relative_to(ROOT).as_posix())
    return out


def check_values(
    fields: list[tuple[str, str, int]],
    path: Path,
    known: dict[str, set[str]],
    labels: dict[tuple[str, str], set[str]],
    errors: list[str],
) -> None:
    """Each field's own rules: the severity, the source, the shape, the check, and the length of the prose."""
    for name, value, ln in fields:
        if name == "Severity" and value.strip("` ") not in SEVERITIES:
            errors.append(f"{path.name}:{ln}: severity '{value}' is not high, medium, or low")
        if name == "Source":
            check_source(value, path, ln, known, errors, labels)
        if name == SHAPE:
            check_shape(value, path, ln, errors)
        if name == CHECK and coverage_of(value) is None and value != CHECK_REVIEW:
            errors.append(
                f"{path.name}:{ln}: Check reads '{value}'; it reads '{CHECK_REVIEW}', '{CHECK_FULL}', "
                f"or '`{CHECKER}` decides <the part>; the rest is judged.'"
            )
        if name == "Principle" and len(value.split()) > MAX_PRINCIPLE_WORDS:
            errors.append(f"{path.name}:{ln}: Principle is {len(value.split())} words, limit {MAX_PRINCIPLE_WORDS}")
        if name in ("Look for", "Violation"):
            n = len(SENTENCE_END.findall(value))
            if n > MAX_SENTENCES:
                errors.append(f"{path.name}:{ln}: {name} is {n} sentences, limit {MAX_SENTENCES}")


def check_file(
    path: Path,
    prefix: str,
    known: dict[str, set[str]],
    labels: dict[tuple[str, str], set[str]],
    texts: dict[str, str],
    tagged: dict[Part, str],
    errors: list[str],
    checks: dict[str, tuple[str, str]] | None = None,
) -> int:
    """Check one lens file, whose lenses carry `prefix`; return its lens count.

    `checks` gathers each lens whose Check line names the checker: id -> (coverage, where).
    """
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    for ln, line in enumerate(lines, start=1):
        if NUMBERED.search(line):
            errors.append(f"{path.name}:{ln}: refers to a section by number")
        if len(line) > MAX_COLUMNS:
            errors.append(f"{path.name}:{ln}: {len(line)} columns, limit {MAX_COLUMNS}")
    fenced = {n for n, code in enumerate(fenced_lines(text)) if code}
    expected = 1
    count = 0
    i = 0
    while i < len(lines):
        m = None if i in fenced else HEADING.match(lines[i])
        if not m:
            i += 1
            continue
        count += 1
        pre, num = m.group(1), int(m.group(2))
        lens_id = f"{pre}-{num:02d}"
        where = f"{path.name}:{i + 1}"
        if pre != prefix:
            errors.append(f"{where}: prefix {pre} is not {prefix}, the prefix lenses/README.md lists for this group")
        if num != expected:
            errors.append(f"{where}: expected id {prefix}-{expected:02d}, found {lens_id}")
        expected = num + 1
        fields, i = lens_fields(lines, i, fenced)
        found = [f[0] for f in fields]
        if found not in ORDERS:
            errors.append(f"{where}: fields are {found}, expected {list(FIELDS)}, optionally {SHAPE}, then {CHECK}")
        check_identifiers(lens_id, fields, path, known, texts, errors)
        check_style(lens_id, fields, path, known, tagged, errors)
        check_values(fields, path, known, labels, errors)
        for name, value, ln in fields:
            coverage = coverage_of(value) if name == CHECK else None
            if coverage is not None and checks is not None:
                checks[lens_id] = (coverage, f"{path.name}:{ln}")
    if count == 0:
        errors.append(f"{path.name}: no lenses found")
    return count


def main(argv: Sequence[str] = ()) -> int:
    argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0], allow_abbrev=False).parse_args(list(argv))
    errors: list[str] = []
    known = sections()
    labels = paragraph_labels()
    texts = section_texts()
    tagged = tags(errors)
    groups = listed_groups(errors)
    files = {p.name: p for p in LENSES.glob("*.md") if p.name != "README.md"}
    listed = {filename for _prefix, filename in groups.values()}
    for group, (_prefix, filename) in groups.items():
        if filename not in files:
            errors.append(f"lenses/README.md lists {group} -> {filename}, file missing")
    for name in sorted(files):
        if name not in listed:
            errors.append(f"lenses/{name} is not listed in lenses/README.md")
    for ln, line in enumerate((LENSES / "README.md").read_text(encoding="utf-8").splitlines(), 1):
        if NUMBERED.search(line):
            errors.append(f"README.md:{ln}: refers to a section by number")
    total = 0
    checks: dict[str, tuple[str, str]] = {}
    for _group, (prefix, filename) in sorted(groups.items(), key=lambda g: g[1][1]):
        if filename in files:
            total += check_file(files[filename], prefix, known, labels, texts, tagged, errors, checks)
    rules = registered_rules()
    for lens_id, (coverage, where) in sorted(checks.items()):
        if lens_id not in rules:
            errors.append(f"{where}: {lens_id} says {CHECKER} decides it, and no rule of that id is registered")
        elif rules[lens_id][0] != coverage:
            errors.append(f"{where}: {lens_id}'s Check line says {coverage}, and its rule registers {rules[lens_id][0]}")
    for rule_id, (_coverage, rel) in sorted(rules.items()):
        if rule_id not in checks:
            errors.append(f"{rel}: rule {rule_id} is registered, and lens {rule_id} has no Check line that names {CHECKER}")
    for path in (README, LENSES / "README.md"):
        if not path.exists():
            continue
        for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in COUNT.finditer(line):
                if int(m.group(1)) != total:
                    errors.append(f"{path.relative_to(ROOT)}:{ln}: says {m.group(1)} lenses, the catalog has {total}")
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} problem(s) in {len(files)} lens file(s)")
        return 1
    print(f"lenses ok: {total} lenses in {len(groups)} groups")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
