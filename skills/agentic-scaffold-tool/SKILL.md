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
Failures the Model Reads, Secrets Never Enter a Step), The Runtime,
Identity, Trust, and Attribution (Bound What a Convinced Model Can Do).
Lenses: `../../lenses/tools.md`.

## Input

`<tool_name> --class <class> --effect read_only|idempotent|unsafe [--mode sync|job] [--rate <micros an hour>] [--timeout <seconds>] [--secret <name>] [--kinds <kind,...>]`,
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
  A `job` is `idempotent`, whatever its work spends: a start under a
  key that started before attaches to that work (step 4), so a repeat
  starts nothing.
- `--mode` is `sync` unless the work outlives a run: a long build, a
  load test, a run on a leased machine. Then it is `job`.
- `--rate` is, for a `job` that costs money while it works, the most it
  costs an hour at reference cost, in millionths. A job that leases a
  machine or buys compute has one. When it is not given, ask;
  unattended, stop and report that its rate is missing. Such a job is
  never written without a rate, which would let it spend outside every
  budget.
- `--timeout` is the tool's own ceiling, below the engine's limit,
  `engine_limit` of `ToolsOptions` in
  `om/src/<name>/om/tools/impl/manager.py`. Unattended, it is the
  longest a call of the tool should take. A `job`'s is the longest its
  work should take, which no engine limit caps: the tree's deadline
  does.
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
| `om/tests/unit/test_tool_<tool_name>.py` | the cases of step 6, over the helpers of `om/tests/contracts/tools.py`; a `job` tool's loop cases over `om/tests/contracts/loops.py` |

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
   acts on nothing in particular. The target's `outward` attribute, when
   it answers one, wins. When it says nothing, the class answers: a call
   of a class outside `INWARD_CLASSES` in
   `om/src/<name>/om/tools/rules.py` counts as outward, and
   `DEFAULT_CEILINGS` there holds it for a person whatever a tenant
   allows. So a call of such a class that stays in the session's own
   work, such as a push to the session's own branch or a pull request
   on the bound repository, answers `outward: False`, and a call of an
   inward class that changes external state beyond the session's own
   work product, or reaches past its egress allowlist, answers
   `outward: True`. The shapes `Command` and `PushBranch` carry no such
   mark, so each takes its class's answer. `preflight` refuses a call that cannot
   succeed with `ToolFailed`, before anyone is asked to approve it; its
   runtime is read-only. A tool with nothing to check returns. An error
   the runtime raises is an infra exception, read by its `http_status`,
   never caught by its class, as the guideline's DEL-29 holds.
4. A `job` tool's `run` starts the work and answers `JobStarted` at
   once; it never waits for the work. The work runs under
   `runtime.key`, so a start under a key that started before attaches
   to it, and ends by `runtime.deadline`. The handle is the tool's own
   name for the work, at most 200 characters, and the work's system
   keeps the key beside it. That system reports the end, naming the key
   and the handle, through `complete_job` of the loop
   (`om/src/<name>/om/agents/loop.py`), as a `JobCompletion` with what
   it says, the failure's class when it failed, and its cost when it
   knows it. The product wires that report to the system, the way it
   wires any event from outside. A report can come before the loop has
   written its park, and `complete_job` refuses it as `NotFound`: the
   wiring tries it again, backing off, until the job's deadline, and
   drops it after, when the loop has answered the call as out of time.
   `cancel` ends the work, and a cancel of work that ended already does
   nothing. With `--rate`, the spec declares it as
   `rate_micros_per_hour`: the loop holds that rate until the job's
   deadline before the work starts.
5. `run` answers the output model, a job's `JobStarted`, or raises
   `ToolFailed` with the class of `ToolFailure` that fits, decided
   where the failure happens. A result the call exists to report, such as a test that fails, is the
   output, not a failure. For a job, the class is chosen for the
   model's next move alone: any failure of `run` keeps the job's hold
   whole, since the work may have started. Only `JobRefused`, raised
   before `run` starts anything, as when the work's system refuses the
   job, releases it. A secret goes by name: declared in the spec's
   `secrets` as a `SecretUse`, and passed by name to `runtime.run`.
   An `unsafe` tool runs one command a call.
6. The tests build the tool and run it through the tools manager over
   the twin transport, as the loop runs a call:
   - a registry holds it, `ToolRegistry([...])`, with the product's
     class declared in `domain_classes` when it has one, and the schema
     it renders refuses unknown fields;
   - a call `gate` lets run, shape the gate's cases in
     `om/tests/unit/test_tool_policy.py`, then `execute` answers its
     output, shape `om/tests/unit/test_tool_execution.py`; `execute`
     refuses a `job` tool, whose cases are the last bullet's;
   - each `ToolFailed` the tool's own code raises, and its preflight's
     refusal when it has one;
   - its target read from the system whatever the input claims, when it
     has one, shape the target case of `om/tests/unit/test_tool_policy.py`;
   - an outward call, under a tenant layer that allows its class, still
     decided `APPROVE`, shape `test_no_tenant_rule_loosens_a_call_past_a_ceiling`
     in that file;
   - by its effect after a crash, shape `om/tests/unit/test_tool_recovery.py`:
     a repeatable call runs again under the same key, and an `unsafe`
     one is answered from the transport's record and never run again;
     a `job` tool's is in the next bullet;
   - for a `job` tool, through `start_job` and `cancel_job` of the
     tools manager: its start under the call's key; its crash case, a
     second start under that key that attaches to the work and starts
     none; each `JobRefused`, answered as `JobNotStarted`; and its
     cancel, shape `test_a_job_starts_by_a_deadline_no_later_than_the_trees`
     in `om/tests/unit/test_tool_registry.py`. Then, through the loop,
     the tool over a double of the work's system that keeps its starts
     and cancels, as `Build` in `om/tests/contracts/loops.py` keeps
     them, joined to the loop's catalog by `loop_over`'s `extra`, with
     a kind in `kinds` that names it: the cases of
     `om/tests/unit/test_loop_jobs.py`: its call parks the loop on the
     job and a completion answers it, its deadline cancels it, a
     cancel cancels it, and with `--rate`, a refused hold starts
     nothing.
7. With `--kinds`, read `../agentic-scaffold-agent-kind/SKILL.md` and
   change each kind as it says. A kind that does not exist yet is
   written by that skill first. Its gates are not run there.
8. A class of the product's own is declared wherever a registry is
   built, as `domain_classes`, as `om/tests/unit/test_tool_registry.py`
   declares one. When a call of that class must always wait for a
   person, add its row to `DEFAULT_CEILINGS`.

Then the gate, `make check`, as After writing in the conventions
runs it.

## Output

As `../_shared/scaffold-conventions.md` states.
