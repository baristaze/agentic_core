---
name: agentic-deviate
description: "Record a deliberate deviation from a rule of the agentic_core spec as an ADR under docs/adr/: the rule quoted, the decision, the consequences. Use when a finding of an engine review is accepted as intentional."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(date:*), Bash(git ls-tree:*)
---

# agentic-deviate

A project built on the engine may still need to diverge from a rule of
the spec. The divergence is recorded, not argued about in review
threads: one ADR quotes the rule, states the decision, and names what
the project accepts in exchange. A breach whose ADR is cited next to
the code is a documented exception. An engine review
(`agentic-review-<group>`) reports it on one line under Deviations. It
is not a finding, and it never lowers a severity.

## Input

The arguments name the rule being deviated from, as a lens id
(`STP-02`), a section of the spec by title (`Bounds and Budgets, One
Gate, Before the Call`), or a sentence describing it, optionally
followed by a one-line reason. Ask in one message for what is missing:
the reason, the scope of the deviation (which namespace, tool, or agent
kind), and whether it is permanent or has a condition for ending.

Three things are no deviation from the spec. When the arguments
describe one, say which, and stop:

- A rule of the guideline: a lens id of the guideline's (`STO-02`), or
  a section of the guideline. The guideline's own `arch-deviate`
  records it.
- A section tagged `default`: a named choice an adopter may substitute.
  Only a departure from untagged text is a deviation.
- A section tagged `core`: an invariant, and a departure from it is a
  different engine, never a deviation.

## Procedure

1. Resolve the rule: find the lens in `../../lenses/` and the section
   it cites in `../../agentic_core_spec.md`, paths from this skill's
   folder as `realpath` resolves it. Quote the principle verbatim: the
   lens's Principle, or the section's Principle box when no lens holds
   the rule. Read the tag alone on the line under the heading that
   states the rule, if any: a tag covers its own heading's text, never
   the subsections under it. A lens's first Source is the section that
   states its rule, so when a lens cites several sections, the first
   one's tag decides. When the rule itself allows what the arguments
   describe, such as an `optional` section whose trigger has not
   arrived, there is nothing to record: say so, quote the words that
   allow it, and stop.
2. The ADR goes under `docs/adr/` at the root of the repository you
   run in, created when it does not exist: a review opens the record
   there by the number cited beside the code. A project's own records
   take a thousand no upstream layer uses, so a later release of its
   base never brings the same number. The records the base holds are
   upstream: the files of the `docs/adr/` folder on the `scaffold`
   branch, wherever in that tree the base keeps it
   (`git ls-tree -r --name-only scaffold`, or `origin/scaffold` when
   there is no local branch). The project's thousand is the next above
   the highest upstream number: the engine's records are 1001 to 1999,
   so a project built on it numbers from 2001. The new record is one
   above the highest record already in that thousand, or its first
   number when there is none. When there is no `scaffold` branch, say
   so and stop: only the base tells an upstream number from the
   project's own. Zero-pad the number as the folder's records are
   (`NNNN-<slug>.md`, the slug the title's words in lowercase, joined
   by hyphens). Refuse to write a path that already exists.
3. Take the date from `date +%F`: the ADR records the day the decision
   is made, which is today, not the day of the last commit.
4. Write the ADR with the template below, under `docs/adr/` only.
   Keep it under one page.
5. Append one row, the ADR number, the rule, and a one-line summary, to
   the `## Deviations` table of the file under `specs/` that points at
   this spec: it names `agentic_core_spec.md` and has that table. When
   more than one does, the row goes in the one whose table already
   holds the engine's rows (ADRs 1001 to 1999), else in
   `specs/architecture.md` when it is one of them, else in the first of
   them in path order. A file that points at the guideline alone
   records the guideline's deviations, not this one. When no file
   qualifies, there is no deviations table.
6. An `agentic-check` entry goes only with a finding the checker
   reports. The lens's `Check` line says which part that is: the whole
   lens when it reads "decides it", and only the part it names when it
   ends "the rest is judged". A deviation in a judged part, or under a
   lens whose `Check` line reads `review`, gets no entry: the checker
   reports nothing there, and an entry that matches no finding is
   itself a finding that fails the gate. The ADR, the Deviations row,
   and the citation beside the code are its record. For a deviation in
   the part the checker decides, the ADR alone does not pass the gate:
   give it the entry that names the ADR, in the shape
   `../../checkers/README.md` shows (Exceptions), whose rule id is the
   lens id. A whole rule turned off is a `[[tool.agentic-check.disable]]`
   entry with `rule`, `adr` (the ADR's path), and `reason`. A rule
   broken in some files is a `[[tool.agentic-check.exception]]` entry
   with `rule`, `path` (a glob), `adr`, and `reason`. Append it to the
   root `pyproject.toml` when that has a `[tool.agentic-check]` table,
   and print it otherwise. Write no inline ignore comment: the checker
   reads none.
7. Tell the person to cite `ADR-NNNN` in a comment beside the code that
   deviates. A review reports the code under Deviations, and not as a
   finding, only when the ADR is cited there.

Do not commit. Do not edit the spec or the lenses; a deviation belongs
to the project, not to the rule. Edit nothing but the ADR folder, the
deviations table, and the `[tool.agentic-check]` entry.

## Output

The path of the new ADR, the row appended to the deviations table (or
"no deviations table"), the `agentic-check` entry appended or printed
(or "no checker entry"), the reminder to cite `ADR-NNNN` beside the
code, and the one-line summary for the reviewer. Nothing else.

## ADR template

```markdown
# ADR NNNN: <title that names the deviation>

**Status**: accepted (<date>)

## Context

<The rule: lens id, section of the agentic_core spec, and the principle
quoted verbatim. Why it does not fit here. Facts, not preferences.>

## Decision

<What the project does instead, in the present tense. Where it applies.
Whether it is permanent or ends when a named condition holds.>

## Consequences

<What the project accepts: the guarantee it gives up, the test or check
that stands in for it, the reviews that must treat the cited code as an
exception.>
```
