---
name: agentic-review-full
description: "Full engine review: the nine lens groups of the agentic_core spec run in parallel and merge into one report. Use before a pull request to engine code, or when a change crosses groups."
allowed-tools: Read, Grep, Glob, Agent, Bash(git diff:*), Bash(git show:*), Bash(git log:*), Bash(git status:*), Bash(git rev-parse:*), Bash(git merge-base:*), Bash(git symbolic-ref:*), Bash(git ls-files:*)
---

# agentic-review-full

Run every lens group over the same scope, in parallel, and merge the
nine reports into one. Each group is judged by its own reviewer so that
no perspective is diluted by another; this skill only fans out,
collects, and merges. It judges the engine's rules. The guideline's own
rules, which the spec cites and does not repeat, are the guideline's
`arch-review-full`'s to judge, from the guideline's plugin.

## Input

The arguments name what to review, exactly as `agentic-review-<group>`
reads them (see `../agentic-review-steps/SKILL.md`, Input; a path that
starts with `../` is read from this skill's folder as `realpath`
resolves it). Resolve the scope once, here, into a concrete
description (the list of files, or the range or commit) and hand the
same description to every reviewer so the nine reports cover the same
ground. A range or a commit is handed over as the ref, with its list of
files, and the reviewer reads each file at the range's end or the
commit, never from the working tree. `all` and a path or a glob,
which can be large, are handed over as the path or the glob (`.` for
`all`), the commit `HEAD` is at, and the count of files the
`git ls-files` call gave, never as a list: each reviewer lists them
itself with the same call, and reads the working tree, never that
commit, which only records where `HEAD` stood. An empty
scope is reported as "nothing to review" and the skill stops. `all`
costs nine full reads of the repository, one per reviewer; a path or
a range is the cheaper question whenever the change is narrower than
the tree.

## Procedure

1. Resolve the scope and write it down in one line.
2. Take this skill's folder from the base directory the host names when
   it loads the skill, and make the paths from it absolute: the lens
   catalog is `../../lenses/` and the spec is
   `../../agentic_core_spec.md`. Run no command to find the folder:
   `realpath` is not pre-approved, so an unattended run would stop on a
   prompt. Reviewers do not see this skill's text, so pass them
   absolute paths.
3. Where the agent can start subagents, launch nine reviewers at once,
   one per group, each with the scope line, the group name, the
   absolute path of its lens file (`<catalog>/<group>.md`), and the
   absolute path of the spec. Use the `agentic-reviewer` agent that
   `../../agents/agentic-reviewer.md` defines for Claude Code
   (`agentic-core:agentic-reviewer` when installed as the plugin). When
   no such agent is installed, give a general subagent the scope line
   (it stands for the skill's arguments), the group name, the absolute
   paths of the lens file and the spec, and the text of
   `../agentic-review-<group>/SKILL.md` with every path in it that
   starts with `../` made absolute from that skill's folder, which sits
   beside this skill's folder, first, since the subagent reads it from
   elsewhere. Tell it that the run is read-only (it never edits,
   stages, or commits, and never runs a file of the repository under
   review), and that it returns its report within 80 turns, the cap
   `agentic-reviewer` holds. Where the agent has no subagents, run the
   nine group procedures one after another in this session. The groups:
   - `agentic-review-steps`
   - `agentic-review-windows`
   - `agentic-review-models`
   - `agentic-review-tools`
   - `agentic-review-live`
   - `agentic-review-trust`
   - `agentic-review-agents`
   - `agentic-review-bounds`
   - `agentic-review-privacy`
4. Wait for all nine. A report follows the group format when every
   section of it is there (the title, the Scope and Lenses lines,
   Findings, Deviations, Passed, Unverified, Not applicable) and its
   counts add up: applied is passed plus findings plus unverified, and
   applied plus not applicable is the number of `## <LENS-ID>`
   headings in its lens file. A reviewer that fails (an error, or no
   report within its turns), or returns a report that does not follow
   the group format, is re-run once with the same message, as soon as
   it returns; if it fails again, its group is reported as "not
   reviewed" with the error.
5. Merge:
   - First write every path repository-relative, as `git ls-files`
     prints it at the root: no leading `./`, no absolute prefix, no
     `<ref>:`. The matching and sorting below compare these.
   - Concatenate all findings and sort by severity (high, medium, low),
     then by file and line.
   - When two findings, from two groups or one, flag the same
     `path:line`, keep both lens ids on one line; the fix text comes
     from the higher-severity one. At equal severity the group whose
     opening paragraphs (the top of its lens file) own the rule wins the
     fix text, and the other id stays on the line. Within one group at
     equal severity, the lower lens id wins, and between two lines of
     one lens, the first by file and line. Each id stays on the line
     once, the winner's first.
   - Two findings whose fixes change the same symbol (the class,
     method, or setting the Fix sentence edits, never one it only
     names) merge into one line the same way, whatever their
     `path:line`; the line named is the winner's.
   - Concatenate every group's Deviations lines under Deviations, in
     lens id order, or `None.` when there are none. They are not
     findings and count nowhere.
   - Count applied, passed, findings, unverified, and not-applicable
     lenses across groups. Applied is passed plus findings plus
     unverified; applied plus not applicable is the size of the
     catalog, so every lens is counted once.
6. Write the merged report below. Then, if the report has three or
   more `high` findings, say so in one sentence after the report,
   with the count. Nothing else.

Never edit, stage, or commit, and never run a file of the repository
under review. This skill reads and reports.

## Output

The group report shape, plus a `Groups` line and a per-group table:

```markdown
# Engine review

**Scope.** <the scope line>
**Groups.** steps, windows, models, tools, live, trust, agents, bounds, privacy
**Lenses.** <n> applied, <p> passed, <f> findings, <u> unverified, <x> not applicable

## Findings

- **<LENS-ID>[, <LENS-ID>] <severity>** `<path>:<line>` <what breaks the rule>. Fix: <one sentence>.

## Deviations

- **<LENS-ID>** `<path>:<line>` ADR-NNNN <what the ADR accepts, a few words>.

## By group

| Group    | Applied | Passed | Findings | Unverified | Not applicable |
|----------|---------|--------|----------|------------|----------------|
| steps    |         |        |          |            |                |
| windows  |         |        |          |            |                |
| models   |         |        |          |            |                |
| tools    |         |        |          |            |                |
| live     |         |        |          |            |                |
| trust    |         |        |          |            |                |
| agents   |         |        |          |            |                |
| bounds   |         |        |          |            |                |
| privacy  |         |        |          |            |                |

## Passed

<LENS-ID>, <LENS-ID> (`<path>`), ... (all groups, in id order; a `high` lens names the file that proved it)

## Unverified

<LENS-ID> (<what would decide it>), ...

## Not applicable

<LENS-ID> (<why>), ...
```
