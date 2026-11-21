---
name: agentic-scaffold-agent-kind
description: "Add an agent kind, or a new version of one: its tools, its done rule and result tool, its authority mode, who may start it, and its tree's bounds, in a folder of its own with each version's prompt in Markdown pinned by its lock, registered with every process that builds the managers, with its tests, in the shape of the engine's agent kinds. Python."
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

`<kind> [--done answer|result_tool] [--result-tool <tool>] [--tools <tool,...>] [--authority delegated|steady] [--started-by person|agent|both] [--tree <height>/<count>] [--deadline <hours>] [--share <micros>|tokens=<count>]`,
and what the kind's agent does, in the arguments or in the
conversation.

Example: `fixer --done result_tool --result-tool submit --tools read_log,run_tests,submit --authority steady --started-by both --tree 1/0 --deadline 8`.

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
- `--started-by`: who may start a session of the kind, as
  `AgentKind.started_by` holds it: `person` (the default) for a kind a
  person starts or a hand-off reaches, `agent` for one only a spawn
  starts, `both` for either. When it is not given, it follows `--share`:
  a kind that names a share is `both`, and one that names none is
  `person`.
- `--tree`: the tree's height and its count of sub-agents; `1/0`, a
  single agent, unless the kind spawns.
- `--deadline`: the hours a tree the kind roots has, from its start;
  none unless the product bounds the kind's work in time.
- `--share`: what one session of the kind may spend over its life as a
  sub-agent, as `Amount` holds it: a bare `<micros>` is reference cost in
  millionths, `Amount(cost_micros=<micros>)`, and `tokens=<count>` is
  native tokens, `Amount(tokens=<count>)`. A share covers at least one
  call's worst-case hold (Hold, in `om/src/<name>/om/spend_budgets/README.md`):
  a smaller one refuses the child's first call, and only a person clears
  it. A kind names a share when a spawn can start it, which is known when
  the kind is written: every kind that delivers through a result tool,
  and any kind the arguments say a spawn starts, which names `agent` in
  `started_by` too. An assistant a person starts directly names none:
  its `share` is left unset, never `Amount(cost_micros=0)`, and a spawn
  of it is refused. When it is not
  given for a kind that names one, ask; unattended, the kind takes the
  largest share another kind of the catalog names, and when none names
  one it leaves the share unset and the report says a spawn of the kind
  is refused until a person names it.

## Created

The shape of a kind is the example assistant's folder,
`services/api/src/<name>/services/api/kinds/assistant/`, over
`AgentKind` in `om/src/<name>/om/agents/types/kind.py` and the prompt
files of `om/src/<name>/om/agents/prompts.py`. Its fields beyond the
example's are those of `DELIVERY` and `ASSISTANT` in
`om/tests/unit/test_agents.py`.

| File | Holds |
|------|-------|
| `om/src/<name>/om/agents/kinds/__init__.py` (the first kind) | `AGENT_KINDS`, every version of every kind folder beside it, read from each folder's `VERSIONS` |
| `om/src/<name>/om/agents/kinds/<kind>/__init__.py` | a docstring naming the kind |
| `om/src/<name>/om/agents/kinds/<kind>/kind.py` | an `AgentKind` constant for each version, its prompt read with `prompt_of(__file__, <version>)`, and `VERSIONS`, every version the product still runs |
| `om/src/<name>/om/agents/kinds/<kind>/prompts/v<version>.md` | the version's prompt, in Markdown |
| `om/src/<name>/om/agents/kinds/<kind>/prompts/lock.json` | the digest of each released version's prompt, by file |
| `om/tests/unit/test_agent_kinds.py` (the first kind) | the cases of step 5 |
| `workers/maintenance/tests/test_container.py` (the first kind) | the worker's container passes `AGENT_KINDS` |

## Changed

| File | Change |
|------|--------|
| `om/src/<name>/om/agents/kinds/<kind>/kind.py`, `prompts/`, and `prompts/lock.json` | a new version: its constant, its prompt file, its lock entry, and its place in `VERSIONS` |
| `services/api/src/<name>/services/api/container.py`, `workers/maintenance/src/<name>/workers/maintenance/container.py` (the first kind) | `agent_kinds=AGENT_KINDS` in the `AgentRootOptions` its call to `build_managers` passes |
| `om/tests/unit/test_agent_kinds.py` | the kind's cases |
| `services/api/tests/test_container.py` (the first kind) | the API's container passes `AGENT_KINDS` |
| `om/src/<name>/om/agents/README.md` | the kind, in the product's language |

## Procedure

1. The engine holds a product's kinds in one catalog, which
   `build_agent_managers` in `om/src/<name>/om/agent_root.py` takes as
   `AgentRootOptions.agent_kinds`. Every process's container that
   builds the managers passes `AGENT_KINDS` in the `agent_root_options`
   of its `build_managers` call, so each process knows the same kinds.
2. A kind is versioned, and a session keeps the version it started on.
   A kind is a folder of its own beside the others, never inside one.
   Its prompt is the Markdown of `prompts/v<version>.md`, which the
   constant reads at import; it is never a string in code. A change to
   a kind that the last commit holds
   (`git show HEAD:om/src/<name>/om/agents/kinds/<kind>/kind.py`) is the
   next version: a constant of its own beside the last, a prompt file
   of its own, and a lock entry of its own, and both versions stay in
   `VERSIONS` while a session may run the last. A released version's
   prompt file and lock entry never change, and a lock entry is never
   removed, even when its version goes. A new version's lock entry is
   the one `prompt_breaches` names, written once its text is final. A
   kind only this work wrote changes in place, its lock entry with it.
3. A kind names its tools by name, and holds no more power than they
   give. Each tool it names that the tree does not have is written
   first: read `../agentic-scaffold-tool/SKILL.md` and follow it. A
   result tool's input holds a `Result`, from
   `om/src/<name>/om/agents/types/result.py`. The gates of that skill
   are not run there: this skill's run once, for both.
4. A kind's bounds are the tree's: its height, its count, and one
   deadline every session of it shares. A kind that spawns sub-agents
   names a `spawn`-class tool among its tools. A kind a spawn can start
   names `Starter.AGENT` in its `started_by` and its `share`, an `Amount`
   from `om/src/<name>/om/spend_budgets/types/amount.py`: a spawn writes
   it as a budget on the child's session, and refuses a kind with
   either missing. Its
   parent holds what a spawn asks of it: a `spawn`-class tool, the kind's
   result tool among its tools, and a tree with room for the child, a
   height above the parent's depth and a count above 0. The kind that
   lacks one, the parent or the root of its tree, is changed as step 2
   says.
5. The tests hold, over `AGENT_KINDS`: `prompt_breaches` finds nothing
   in any kind folder, as `services/api/tests/test_assistant_api.py`
   runs it over the example's; the catalog builds from them;
   `latest(<kind>)` answers this version, and each earlier version
   still answers by its number; `latest(<kind>).share` is the share the
   kind names, or `None` for a kind that names none; and a start the
   kind's `started_by` does not allow is refused. Each container passes
   them, held in that process's own tests, read from the `agent_root_options` its
   `build_managers` call receives, as `services/api/tests/test_container.py`
   reads that call.

Then the gate, `make check`, as After writing in the conventions
runs it.

## Output

As `../_shared/scaffold-conventions.md` states.
