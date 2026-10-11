# ADR 1031: A kind is built from toolsets, and a deferred toolset loads as a recorded switch

**Status**: accepted (2026-10-11)

## Context

A kind lists its tools one by one, and every version repeats the list.
A role is mostly the tools it holds, and some roles hold many: dozens of
read tools, or every tool a server offers. Each tool definition rides
every request of every session of the kind, so a large registry is paid
for on every call, and a model chooses worse among many tools.

The prompt cache holds only while the bytes before its breakpoint hold.
Tool definitions come first in a request, so any change to them, by a
single byte, rewrites the whole cached prefix.

## Decision

**A toolset.** A toolset is data: a name, a version, a one-line summary,
and its tools, by name. A product declares toolsets beside its kinds. A
kind names toolsets, single tools, or both. Its `tools` is their union,
with its deferred toolsets' tools and `load_toolset` folded in: every
tool a session of it may come to call. So every check that reads a
kind's power reads them too: the enqueue rule, a sub-agent's cut, the
rule of two, and the session's pin. The registry renders sorted by
name, so a kind rebuilt from toolsets with the same tools renders the
same bytes. A catalog refuses a version of a toolset declared with two
different contents.

**A deferred toolset.** A kind may defer a toolset. A tool waits when a
deferred toolset holds it and no toolset held from the start does; a
tool named alone that a deferred toolset holds waits too, so a kind
folded again stays the same. A waiting tool is out of the registry, so
it is out of every request and no call reaches it. A kind that defers
holds `load_toolset`. Its description is a fixed text and one line per
deferred toolset, its name and its summary, in the kind's order. The
description stays the same for the session's life, a loaded toolset's
line included, so nothing before a load's tools changes.

**A load.** The loop answers `load_toolset` after the gate, as it
answers a result tool, since only the loop holds the registry. The
answer and a `switched` step go in one append. The step names the
toolset, its version, and the tools it added, and references the call.
A load adds the toolset's tools the session may call that its registry
does not hold yet, sorted by name. A load that adds nothing writes no
switch, and a toolset the kind does not defer is the call's invalid
input. A loaded toolset stays loaded for the session. Its tools join
the next request: a call the load's own turn makes to one of them is
refused as a tool not offered, by a run that settles it after a lost
one as well.

**The render.** A run's registry is a projection of the session and its
history: the tools the session may call that wait for no load, the
tools its loads added, and `load_toolset` while any tool waits. The
load adds no power of its own, so a sub-agent cut to a parent that lacks
it still loads what its own tools allow. The tools a load added render
after every tool before them, in the order the loads happened. So the
next request's tool definitions are the last one's with the new tools
after them. The load costs one cache write, and its switch records it,
as a fill switch's does. Nothing else changes a session's tool
definitions, and a replay of the history renders the same requests.

**Native deferral, not used yet.** The Anthropic Messages API defers
tool definitions natively. A definition marked `defer_loading` stays
out of the cached prefix until a `tool_reference` in a tool's result
expands it. It needs no beta header, and every model the engine prices
supports it. The adapter speaks HTTP, so there is no SDK to pin. The
engine does not use it yet, for two reasons. A reference lives only in
a tool's result, so a summary that folds a load's answer loses the
expansion, and the render would need to move those tools into the
prefix at that summary: a design of its own. And the wire shape, such
as a result that holds text and references together, shows only on a
billed call. Every provider takes the recorded switch.

Trigger: the cache writes paid at loads, read from the usage of the
request after each toolset switch, pass 5% of the model spend of the
sessions that load.

## Consequences

- A kind version pins its toolsets at their versions. A toolset's new
  version reaches a kind through the kind's next version.
- Sessions of a kind version share one cached prefix until a load.
  Sessions that load the same toolsets in the same order share one
  again.
- A `switched` step names a fill switch or a toolset, never both. A
  reader of `fills` checks that it is there.
- Nothing is migrated: a step's header is stored as JSON, and a
  session's tools are the kind's, as before.
