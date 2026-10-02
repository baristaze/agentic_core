---
name: agentic-scaffold-agent-kind
description: "Add an agent kind, or a new version of one: its tools, its done rule and result tool, its authority mode, and its tree's bounds, declared in the product's catalog and registered with every process that builds the managers, with its tests, in the shape of the engine's agent kinds. Python."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(make check), Bash(uv run:*), Bash(git status:*), Bash(git show:*)
---

# agentic-scaffold-agent-kind

A path that starts with `../` is read from this skill's folder as
`realpath` resolves it.
Conventions: `../_shared/scaffold-conventions.md`.
Sections of `../../agentic_core_spec.md`: Agent Kinds and Sub-Agents
(Agent Kinds, Done Rules and the Result Gate, Sub-Agents), Identity,
Trust, and Attribution (Who Is Who), Tools (The Registry).
Lenses: `../../lenses/agents.md`.

## Input

`<kind> [--done answer|result_tool] [--result-tool <tool>] [--tools <tool,...>] [--authority delegated|steady] [--tree <height>/<count>] [--deadline <hours>]`,
and what the kind's agent does, in the arguments or in the
conversation.

Example: `fixer --done result_tool --result-tool submit --tools read_log,run_tests,submit --authority steady --tree 2/3 --deadline 8`.

- `<kind>` is the kind's name, snake case, as `AgentKind.name` holds it.
  `<KIND>` is its constant in upper snake case.
- `--done`: `answer` for an assistant, whose turn with no tool call is
  the answer; `result_tool` for an agent that delivers work, which only
  its result tool ends. A `result_tool` kind names its result tool among
  its tools.
- `--authority`: `delegated` runs each call under the asking person's
  live permissions; `steady` under one principal fixed when the session
  is made. When it is not given, ask; unattended, an agent a person
  talks to is `delegated`, and one that works on its own is `steady`.
- `--tree`: the tree's height and its count of sub-agents; `1/0`, a
  single agent, unless the kind spawns.
- `--deadline`: the hours a tree the kind roots has, from its start;
  none unless the product bounds the kind's work in time.

## Created

The shape of a kind is `DELIVERY` and `ASSISTANT` in
`om/tests/unit/test_agents.py`, over `AgentKind` in
`om/src/<name>/om/agents/types/kind.py`.

| File | Holds |
|------|-------|
| `om/src/<name>/om/agents/kinds.py` (the first kind) | the product's kinds, each an `AgentKind` constant, and `AGENT_KINDS`, every version the product still runs |
| `om/tests/unit/test_agent_kinds.py` (the first kind) | the cases of step 5 |

## Changed

| File | Change |
|------|--------|
| `om/src/<name>/om/agents/kinds.py` | `<KIND>`, and its version in `AGENT_KINDS` |
| `services/api/src/<name>/services/api/container.py`, `workers/maintenance/src/<name>/workers/maintenance/container.py` (the first kind) | `agent_kinds=AGENT_KINDS` in the call to `build_managers` |
| `om/tests/unit/test_agent_kinds.py` | the kind's cases |
| `services/api/tests/test_container.py`, a test of the worker's container in `workers/maintenance/tests/` (the first kind) | the container passes `AGENT_KINDS` |
| `om/src/<name>/om/agents/README.md` | the kind, in the product's language |

## Procedure

1. The engine holds a product's kinds in one catalog, which
   `build_managers` in `om/src/<name>/om/root.py` takes as
   `agent_kinds`. Every process's container that builds the managers
   passes `AGENT_KINDS`, so each process knows the same kinds.
2. A kind is versioned, and a session keeps the version it started on.
   A change to a kind that the last commit holds
   (`git show HEAD:om/src/<name>/om/agents/kinds.py`) is the next
   version, a constant of its own beside the last, and both stay in
   `AGENT_KINDS` while a session may run the last. A kind only this
   work wrote changes in place.
3. A kind names its tools by name, and holds no more power than they
   give. Each tool it names that the tree does not have is written
   first: read `../agentic-scaffold-tool/SKILL.md` and follow it. A
   result tool's input holds a `Result`, from
   `om/src/<name>/om/agents/types/result.py`. The gates of that skill
   are not run there: this skill's run once, for both.
4. A kind's bounds are the tree's: its height, its count, and one
   deadline every session of it shares. A kind that spawns sub-agents
   names a `spawn`-class tool among its tools.
5. The tests hold, over `AGENT_KINDS`: the catalog builds from them;
   `latest(<kind>)` answers this version, and each earlier version
   still answers by its number. Each container passes them, held in
   that process's own tests, read from the arguments its
   `build_managers` call receives, as `services/api/tests/test_container.py`
   reads that call.

Then the gate, `make check`, as After writing in the conventions
runs it.

## Output

As `../_shared/scaffold-conventions.md` states.
