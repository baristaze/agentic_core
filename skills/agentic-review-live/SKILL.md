---
name: agentic-review-live
description: "Engine review through the Live lenses of the agentic_core spec: Streams; Steering. For a change in this area, or as one leg of agentic-review-full."
allowed-tools: Read, Grep, Glob, Bash(git diff:*), Bash(git show:*), Bash(git log:*), Bash(git status:*), Bash(git rev-parse:*), Bash(git merge-base:*), Bash(git symbolic-ref:*)
---

# agentic-review-live

Judge the code from one perspective only: the lenses in
`../../lenses/live.md`. Other perspectives have their own skills; do
not borrow their rules, and do not flag anything a lens in this file
does not name. The spec is at `../../agentic_core_spec.md` when a lens
needs its source read in full. A path that starts with `../` is read
from this skill's folder as `realpath` resolves it. If either file is
missing, stop and say the installation is incomplete.

This pass covers Streams; Steering, sections of the spec. A lens that leans on
a rule of the guideline judges the engine's rule only: the guideline's
own rule is its own review's (`arch-review-<group>`, from the
guideline's plugin), never a finding here.

## Input

The arguments name what to review. Read them as the first of these
that matches:

1. Empty: the current branch's change. The default branch is
   `origin/HEAD` when set, else `main`, else `master`. The scope is
   every file changed since the merge base of `HEAD` and the default
   branch (`git diff --name-only <base>`, committed and uncommitted
   alike), plus every untracked file git does not ignore
   (`git status --porcelain --untracked-files=all`). When there is no
   merge base (no default branch, a shallow clone, or unrelated
   histories), the scope is the uncommitted change against `HEAD` plus
   the untracked files, and the Scope line says the merge base was not
   found. In a repository with no commit, it is every file not
   ignored.
2. The word `all`: every file in the repository that git does not
   ignore, tracked or untracked. Expect this to take a while.
3. A range (`main..HEAD`, `main...HEAD`): the files that range
   changes, read as they are at its end.
4. A commit that `git rev-parse --verify --quiet "<arg>^{commit}"`
   resolves (a SHA, a tag, a branch): the change that commit made,
   against its first parent.
5. A path or a glob: every file under it as it is now, untracked files
   included. A path that does not exist is an error; say so and stop.
   A name that is both a commit and a path reads as the commit; write
   `./<name>` for the path.

The empty scope, `all`, and a path read the working tree. A range and
a commit read history, which may not be checked out: read each file at
the range's end or the commit with `git show <ref>:<path>`, never from
the working tree. When the scope resolves to no files, report "nothing
to review" in the report's Scope line and stop.

Read changed files in full, not only the changed lines. Rules break in
the interaction between the new code and its neighbors, so pull in the
interface a class implements, the root that wires it, and the callers
of a changed signature.

## Procedure

1. Read the lens file end to end before looking at any code.
2. Establish the scope and list the files in it.
3. For every lens, in id order, decide one of: **finding** (evidence
   of a breach, with a file and line), **pass** (the lens applies and
   the code satisfies it), **not applicable** (nothing in scope touches
   what the lens judges), or **unverified** (the lens applies, and what
   would decide it lies outside the scope and the neighbors the Input
   section says to read; name what would decide it). A lens whose
   `Check` line reads `review` is judged here whole: no program decides
   any part of it. Keep the "Look for" and "Violation" text of the lens
   in front of you while deciding. When the lens has a `Shape` line,
   open the scaffold file or folder it names, `../../<path>`, and
   compare the code with it: it is the rule as code, so a difference
   shows where to look. The decision still rests on the lens's
   Violation, never on a difference alone.
4. Verify every finding against the real source: open the file, confirm
   the line, confirm the surrounding code does not already handle it.
   Drop a finding you cannot point at. Verify a **pass** on a `high`
   lens the same way: open the file that would breach it and name that
   file in the report; a high lens passes on evidence, never on the
   absence of a finding, and a high lens whose evidence is out of
   reach is unverified, never passed.
5. Assign severity from the lens. Lower it only when the breach is
   contained: in a test double, or under an exception the spec itself
   names. A breach whose ADR quotes the rule and is cited next to the
   code (`ADR-NNNN`, the record `docs/adr/NNNN-*.md` of the repository
   under review) is a documented exception, not a finding. Open the
   record and confirm it quotes the rule; then the breach goes on one
   line under Deviations, and the lens is decided on the rest of the
   scope. An ADR never lowers a severity.
6. Write the report in the format below. Nothing else; no preamble.

Never edit, stage, or commit, and never run a file of the repository
under review. This skill reads and reports.

## Output

```markdown
# Engine review: Live

**Scope.** <what was reviewed, in one line>
**Lenses.** <n> applied, <p> passed, <f> findings, <u> unverified, <x> not applicable

## Findings

- **<LENS-ID> <severity>** `<path>:<line>` <what breaks the rule, one sentence>. Fix: <one sentence>.

## Deviations

- **<LENS-ID>** `<path>:<line>` ADR-NNNN <what the ADR accepts, a few words>.

## Passed

<LENS-ID>, <LENS-ID> (`<path>`), ...

## Unverified

<LENS-ID> (<what would decide it, a few words>), ...

## Not applicable

<LENS-ID> (<why, a few words>), ...
```

Findings are ordered most severe first, then by file. When there are
no findings, the section reads `No findings.`; an empty Deviations or
Unverified section reads `None.` A `high` lens in Passed names the
file that proved it.

The counts on the Lenses line count lenses, never lines. Findings has
one line per breach, so a lens with two breaches has two lines and
counts once in `<f>`. Every lens id in the lens file is decided once:
it counts in exactly one of Findings, Passed, Unverified, and Not
applicable. Deviations lines are not a decision and count nowhere.
Applied is passed plus findings plus unverified, so applied plus not
applicable is the number of lenses in the file.
