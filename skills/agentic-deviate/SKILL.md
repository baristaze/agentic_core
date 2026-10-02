---
name: agentic-deviate
description: "Record a deliberate deviation from a rule of the agentic_core spec as an ADR under docs/adr/: the rule quoted, the decision, the consequences. Use when a finding of an engine review is accepted as intentional."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(date:*)
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
   folder as `realpath` resolves it. Quote the principle verbatim. Read
   the tag alone on the line under the heading that states the rule,
   if any: a tag covers its own heading's text, never the subsections
   under it. When the rule itself allows what the arguments describe,
   such as an `optional` section whose trigger has not arrived, there
   is nothing to record: say so, quote the words that allow it, and
   stop.
2. The ADR goes under `docs/adr/` at the root of the repository you
   run in, created when it does not exist: a review opens the record
   there by the number cited beside the code. Number the new record as
   one more than the highest numeric prefix present, whoever wrote that
   record (the guideline's, the engine's, and the project's own share
   the folder), zero-padded as the folder's records are
   (`NNNN-<slug>.md`). Refuse to write a path that already exists.
3. Take the date from `date +%F`: the ADR records the day the decision
   is made, which is today, not the day of the last commit.
4. Write the ADR with the template below, under `docs/adr/` only.
   Keep it under one page.
5. When a file under `specs/` points at this spec (it names
   `agentic_core_spec.md`) and has a `## Deviations` table, append one
   row: the ADR number, the rule, and a one-line summary. A file there
   that points at the guideline alone records the guideline's
   deviations, not this one.
6. Write no checker entry and no inline ignore comment. A lens whose
   `Check` line reads `review` is judged by the review alone, so no
   program reports the breach and none is told to skip it. The ADR, the
   Deviations row, and the citation beside the code are the record.
7. Tell the person to cite `ADR-NNNN` in a comment beside the code that
   deviates. A review reports the code under Deviations, and not as a
   finding, only when the ADR is cited there.

Do not commit. Do not edit the spec or the lenses; a deviation belongs
to the project, not to the rule. Edit nothing but the ADR folder and
the deviations table.

## Output

The path of the new ADR, the row appended to the deviations table (or
"no deviations table"), the reminder to cite `ADR-NNNN` beside the
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
