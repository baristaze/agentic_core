---
name: agentic-scaffold-tool
description: "Add a tool an agent calls: its input and output, class, effect, timeout, and mode, its target and preflight, its run through the call's runtime, and its tests, in the shape of the engine's tool contract; then name it in the agent kinds that call it. Python."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(make check), Bash(uv run:*), Bash(git status:*), Bash(git show:*)
---

# agentic-scaffold-tool

A path that starts with `../` is read from this skill's folder as
`realpath` resolves it.
Conventions: `../_shared/scaffold-conventions.md`.
Sections of `../../agentic_core_spec.md`: Tools (The Tool Contract, The
Registry, Authorization Classes, Policy, Execution, Long-Running Jobs,
Failures the Model Reads, Secrets Never Enter a Step), The Runtime.
Lenses: `../../lenses/tools.md`.

## Input

`<tool_name> --class <class> --effect read_only|idempotent|unsafe [--mode sync|job] [--timeout <seconds>] [--secret <name>] [--kinds <kind,...>]`,
and what the tool does, in the arguments or in the conversation.

Example: `run_tests --class execute --effect idempotent --kinds fixer`,
for "runs the project's tests in the workspace and answers how many
failed".

- `<tool_name>` is the name the model reads, as `TOOL_NAME` in
  `om/src/<name>/om/tools/types/tool.py` holds it. `<Tool>` is its
  CamelCase.
- `--class` is a `ToolClass` in that file, or a class of the product's
  own, which policy keys on as a name like any other.
- `--effect` is what a repeat of the call does. When it is not given,
  ask; unattended, a call that creates, sends, or charges is `unsafe`.
- `--mode` is `sync` unless the work outlives a run.
- `--timeout` is the tool's own ceiling, below the engine's limit,
  `engine_limit` of `ToolsOptions` in
  `om/src/<name>/om/tools/impl/manager.py`. Unattended, it is the
  longest a call of the tool should take.
- `--secret` names a secret the tool's process needs, by name. Ask how
  it travels: brokered to a destination, or injected into one variable.
- `--kinds` names the agent kinds that call the tool.

## Created

The shape of a tool is `Command` and `PushBranch` in
`om/tests/contracts/tools.py`, over `ToolInterface` in
`om/src/<name>/om/tools/tool.py`. A product's own tools sit beside the
MCP tool, `om/src/<name>/om/tools/mcp.py`, which takes the same
contract.

| File | Holds |
|------|-------|
| `om/src/<name>/om/tools/native/<tool_name>.py` | `<Tool>Input(ToolInput)`, the output model, and `<Tool>ToolImpl(ToolInterface)` with its `ToolSpec`; a `job` tool is a `JobToolInterface` that answers `JobStarted`, shape `Reindex` in `om/tests/unit/test_tool_registry.py` |
| `om/src/<name>/om/tools/native/__init__.py` (the first tool) | empty |
| `om/tests/unit/test_tool_<tool_name>.py` | the cases of step 5, over the helpers of `om/tests/contracts/tools.py` |

## Changed

| File | Change |
|------|--------|
| `om/src/<name>/om/agents/kinds.py` (with `--kinds`) | the tool's name in each kind's `tools`, as `../agentic-scaffold-agent-kind/SKILL.md` changes a kind |
| `om/src/<name>/om/tools/rules.py` (a class of the product's own that must always wait for a person) | a row in `DEFAULT_CEILINGS` |

## Procedure

1. A tool reaches the world only through the runtime its call is
   given: `runtime.run`, `read_file`, `write_file`, and `list_files`.
   It never starts a process, opens a file, or opens a connection of
   its own. A bound system is reached through a client under
   `integrations/`, and a namespace's records through its manager, each
   taken in the tool's constructor, typed by its interface.
2. The spec is what the model reads and what policy keys on. The
   description is a prompt: it says what the call does and what it
   answers, never how policy will treat it. The input refuses a field
   it does not declare, and a field the model fills in to vouch for its
   call is never read by policy. `interruptible` is true unless a stop
   mid-run would leave a half-done effect.
3. `target` reads what the call acts on from the system it acts on,
   never from the input, and answers an empty `Target` when the call
   acts on nothing in particular. `preflight` refuses a call that cannot
   succeed with `ToolFailed`, before anyone is asked to approve it; its
   runtime is read-only. A tool with nothing to check returns. An error
   the runtime raises is an infra exception, read by its `http_status`,
   never caught by its class, as the guideline's DEL-29 holds.
4. `run` answers the output model, or raises `ToolFailed` with the class
   of `ToolFailure` that fits, decided where the failure happens. A
   result the call exists to report, such as a test that fails, is the
   output, not a failure. A secret goes by name: declared in the spec's
   `secrets` as a `SecretUse`, and passed by name to `runtime.run`.
   An `unsafe` tool runs one command a call.
5. The tests build the tool and run it through the tools manager over
   the twin transport, as the loop runs a call:
   - a registry holds it, `ToolRegistry([...])`, with the product's
     class declared in `domain_classes` when it has one, and the schema
     it renders refuses unknown fields;
   - a call `gate` lets run, shape the gate's cases in
     `om/tests/unit/test_tool_policy.py`, then `execute` answers its
     output, shape `om/tests/unit/test_tool_execution.py`;
   - each `ToolFailed` the tool's own code raises, and its preflight's
     refusal when it has one;
   - its target read from the system whatever the input claims, when it
     has one, shape the target case of `om/tests/unit/test_tool_policy.py`;
   - by its effect after a crash, shape `om/tests/unit/test_tool_recovery.py`:
     a repeatable call runs again under the same key, and an `unsafe`
     one is answered from the transport's record and never run again.
6. With `--kinds`, read `../agentic-scaffold-agent-kind/SKILL.md` and
   change each kind as it says. A kind that does not exist yet is
   written by that skill first. Its gates are not run there.
7. A class of the product's own is declared wherever a registry is
   built, as `domain_classes`, as `om/tests/unit/test_tool_registry.py`
   declares one. When a call of that class must always wait for a
   person, add its row to `DEFAULT_CEILINGS`.

Then the gate, `make check`, as After writing in the conventions
runs it.

## Output

As `../_shared/scaffold-conventions.md` states.
