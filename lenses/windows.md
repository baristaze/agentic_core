# Windows

Group id: `windows`. Covers Context of `agentic_core_spec.md`.

This group judges what one model request reads: how it is rendered, the
window it reads and how that window is sized, the pinned zone,
compaction, and large results. It leaves the two trust tiers the
renderer keeps apart to `trust`, the history a compaction never changes
to `steps`, a switch that resizes the window to `models`, and the gate a
compaction passes to `bounds`.

## WIN-01 A request is a pure render of its inputs

**Principle.** A model request is a pure, deterministic function of the
kind version, the fill, the pinned zone, the window, and the new inputs:
the same steps render the same bytes. It is laid out from the most
stable layer to the least: the kind's prompts, the tool definitions in a
fixed order, the pinned zone, the latest summary, the window's steps
since it, then the new inputs and the agent's current plan. A rolling
cache breakpoint sits on the last stable step, within the provider's
limit on breakpoints.

**Source.** Context, Rendering.

**Look for.** The render function and everything it reads; the order of
its layers and of the tool definitions; where the cache breakpoint goes.

**Violation.** A time, a fresh id, or an unordered collection rendered
into a request; tool definitions in an order that varies; the plan or a
new standing instruction rendered above the window, so each change
invalidates the cached window.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/windows/rules.py`

**Check.** review

## WIN-02 A prompt's hash makes a cache regression a query

**Principle.** The `model_request` header records a hash of the
rendered prompt, so a cache regression is a query. Replay drives the
scripted provider with a session's recorded responses, and re-rendering
each recorded `model_request` reproduces its prompt hash; a mismatch is
a rendering regression.

**Source.** Context, Rendering; Testing and Conformance.

**Look for.** The hash on a request's header and what it covers; a
replay test over a recorded history and what it compares.

**Violation.** A request header with no prompt hash, or a hash over
something other than the rendered prompt; no replay test, or one that
re-renders without comparing hashes.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/windows/rules.py` and
`scaffold/acme_root/om/tests/unit/test_windows.py`

**Check.** review

## WIN-03 A window belongs to a request, consistent at both edges

**Principle.** A context window is a run of consecutive steps one model
request reads, consistent at both edges: a cut never separates a tool
request from its response, nor a message from what it answers. A window
is relative to its model, so steps carry no window id, and a
`model_request` records the fill it was sized for and its left edge. The
active window is the main model role's, pinned to the right edge of the
history, its left edge the latest summary or the session's first step. A
side model role reads a consistent suffix sized to its own fill.

**Source.** Context, Windows.

**Look for.** Where a window's edges are cut; any window id on a step;
what a `model_request` records of its window; how a side role's window
is sized.

**Violation.** A cut between a tool request and its response; a window
id stored on steps; a side task handed the main window, or a window
sized for a fill other than its request's.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/windows/rules.py` and
`scaffold/acme_root/om/src/acme/om/steps/types/header.py`

**Check.** review

## WIN-04 A window holds references and sizes, never content

**Principle.** A `ContextWindow` holds its fill, its maximum tokens, its
used tokens, and its edges, by reference. Its used tokens are the size
the provider reported for the last call, plus an estimate for the steps
since. The active window changes with every step, so it is a memory
construct: a snapshot of it holds references and sizes, never content,
may be cached, and is always rebuildable from the steps and the latest
summary.

**Source.** Context, Windows.

**Look for.** The window type and its fields; how used tokens are
computed; any persisted snapshot and what it holds.

**Violation.** A window or a snapshot that holds step content; used
tokens counted locally from the first step when the provider reported a
size; a snapshot that is the only record of a window's edges.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/windows/types/window.py`

**Check.** review

## WIN-05 Only principals feed the pinned zone

**Principle.** The pinned zone carries the session's objective, as a
principal stated or later revised it, and the standing instructions
principals gave, through every compaction. The instructions are built
only from principal-authored messages: quoted verbatim while they fit a
bound, and beyond it as a digest in which each item cites the message it
came from. A summary of the window, which folds in data, is never a
source of instructions and renders as data. The agent's plan is not
pinned; it renders last, as its own notes. The pinned zone changes only
at a summary.

**Source.** Context, The Pinned Zone; Rendering.

**Look for.** The code that builds the pinned zone and what it reads;
whether each digest item cites a message; where the plan and a summary
render.

**Violation.** A standing instruction drawn from a tool result, an
event, an agent's message, or a summary; a digest item with no message
behind it; the plan rendered in the pinned zone.

**Severity.** high

**Shape.** `scaffold/acme_root/om/src/acme/om/windows/rules.py` and
`scaffold/acme_root/om/src/acme/om/windows/types/pinned.py`

**Check.** review

## WIN-06 Compaction is the engine's, automatic and visible

**Principle.** When the active window nears its limit, the engine
compacts it, automatically and visibly: the history and the stream show
it, and the adopter orchestrates nothing. Without compaction, a full
window is a model error. The policy that decides when and how is
injected. A compaction is a model call and passes the budget gate like
any other.

**Source.** Context, Compaction.

**Look for.** What starts a compaction and who calls it; what the
history and the stream show of it; how the policy reaches the engine.

**Violation.** A compaction the adopter must schedule, or one that
leaves no step and no stream part; a policy fixed in the loop's code. (A
summarizer call that skips the gate is BND-02.)

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/windows/impl/manager.py`

**Check.** review

## WIN-07 The default policy trims, summarizes, and retries once

**Principle.** Where the adopter keeps the default policy, it triggers
at a share of the fill's window, measured from the size the provider
reported for the last call plus an estimate, leaving room for the next
response and its tool results. Older bulky tool results render as stubs
with a handle. The summarizer model role folds the
window and the previous summary into a new summary step that references
the range it replaces; the latest exchanges stay verbatim, cut on a
whole exchange, and the pinned zone is rebuilt. A prompt still rejected
as too long compacts once and retries, once per request. A principal may
ask with a compact control, and a switch to a smaller window compacts
first.

**Source.** Context, Compaction (Trigger, Elide, Summarize, Overflow, On
demand, On a switch).

**Look for.** The default policy's trigger; the stub it renders; the
summary step it writes and its references; its overflow retry count.

**Violation.** A trigger read from a local estimate alone; a summary
step with no reference to its range, or a cut inside an exchange; an
overflow retried more than once for one request, or never.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/windows/rules.py` and
`scaffold/acme_root/om/src/acme/om/windows/impl/manager.py`

**Check.** review

## WIN-08 Large results are artifacts, read just in time

**Principle.** A tool result above a size bound is stored as an
artifact. The step holds a preview, its head and tail, and a handle, and
a read tool pages through the rest. Agents retrieve just in time, by
searching the history, reading an artifact, or querying a record,
rather than preload. A sub-agent is the other context tool: it explores
in a clean context and returns a report.

**Source.** Context, Large Results and Retrieval.

**Look for.** The size bound on a tool's output and what happens above
it; the read tool that pages an artifact; what an agent kind preloads
into its first request.

**Violation.** A large result rendered whole into the window; a preview
with no handle, so the rest cannot be read; a kind that loads whole
documents or histories up front instead of a tool to fetch them.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/windows/impl/manager.py` and
`scaffold/acme_root/om/src/acme/om/windows/rules.py`

**Check.** review
