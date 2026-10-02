---
name: agentic-explain
description: "Explain how the agentic_core spec applies to a question, a file, or a change, citing the sections and lenses that govern it. Use when onboarding to engine code or before a change that crosses its parts."
allowed-tools: Read, Grep, Glob
---

# agentic-explain

Answer a question about the engine with the spec as the source of
truth, not with general opinion. The spec is at
`../../agentic_core_spec.md` and the lens catalog at `../../lenses/`,
paths from this skill's folder as `realpath` resolves it. If either is
missing, stop and say the installation is incomplete.

## Input

The arguments are one of:

- a question ("who pays for a sub-agent's model call?", "can a tool
  result instruct the agent?");
- a path to a file or folder in the current repository ("explain what
  rules apply to this tool's module");
- a description of a change ("I want to add a tool that writes to a
  shared drive").

Empty arguments mean: give the guided tour, every section in the order
of the spec's Contents, two sentences each, then the nine lens groups
in one line each.

## Procedure

1. Read the spec's Contents (the list under its `## Contents` heading)
   and the lens group table in `lenses/README.md`.
2. Find the sections and subsections that govern the question. Read
   them in full; quote the `Principle` boxes verbatim when they answer
   the question directly. Name a section's tag when it has one (`core`,
   `default`, `optional`, `style`), as How to Read This in the spec
   defines it: it says whether the rule bends.
3. Find the lenses that a reviewer would apply. Name them by id. When
   a lens has a `Shape` line, name the scaffold file it points to,
   `../../<path>`, and read it: it shows the rule as code, and pointing
   at it beats describing it.
4. When the question is one of general software design, the spec
   defers to the guideline and repeats none of it. Name the guideline
   section the spec cites there (its links are pinned at the release
   the engine builds on), and point at the guideline's `arch-explain`
   for the rest.
5. When the input is a path, open the code and say, for each rule that
   applies, whether the code follows it, in one line each. Do not run
   a full review; point at `agentic-review-<group>`
   (`/agentic-core:agentic-review-<group>` when installed as the
   plugin) for that.
6. When the input is a change, say which section and lens group each
   part belongs to, and which lens a review of it would apply first.
7. When the spec is silent on the question, say so and name the
   nearest section. Do not opine beyond the text.

## Output

Short, in prose, in the spec's voice. Cite sections by title, as
`Section title, Subsection`, never by number, and lenses as `LENS-ID`.
Do not restate the spec at length; quote the sentence that decides,
then stop.
