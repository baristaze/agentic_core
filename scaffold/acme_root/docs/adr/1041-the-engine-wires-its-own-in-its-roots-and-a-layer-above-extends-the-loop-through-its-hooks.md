# ADR 1041: The engine wires its own in its roots, and a layer above extends the loop through its hooks

**Status**: accepted (2026-10-14)

## Context

The engine is built on the guideline's scaffold, and a layer can be built
on the engine's in turn. Each takes its base's releases by a three-way
merge. Where the engine wrote inside the guideline's files, its lines met
the guideline's at every release: the object model's root carried every
engine manager, parameter, and native tool among the guideline's own;
the infra roots built, started, closed, and described each engine
capability inline; `.env.example` held the engine's keys between the
guideline's lines, and the Makefile edited the guideline's lines in
place. A layer above met the same wall one level up: it extended the
loop by editing it, and its edits met the engine's at every release.
The loop was one class in one file, and every layer's edit landed in it.

## Decision

**The engine's wiring lives in files of its own.** `acme.om.agent_root`
builds every manager of the agent from one options object,
`AgentRootOptions`, over the guideline's managers it reads. The
guideline's root calls it once, holds its managers by extending
`AgentManagers`, and registers the session's waiter in the leases'
line. `acme.infra.agent_root` holds the engine's capabilities as one
object, built by one call from each infra root and named once where the
root describes, starts, and closes its own; its settings are the base of
the guideline's. The engine's knobs sit in one block at the end of
`.env.example`, and its Makefile targets in one block at the end of the
Makefile.

**A layer above extends a run through the loop's hooks, never by editing
the loop.** Once a run, before anything renders, the loop asks its hooks
for the toolsets a session holds beyond its kind, and folds them into the
kind, so held ones join the registry and deferred ones the load. Once the
workspace is prepared, before the first model call, it asks them for the
steps to append, under the run's epoch, and tells them whether the loop
is new. A refusal of the session's fill there ends the loop errored, as
the loop's own resolve does. With no hooks wired, the null extends
nothing.

**The loop is split by concern behind one class.** The model turn, the
tool turn with its jobs, parks and waits, recovery after a lost run, and
take-over each live in a module of their own over a base that holds the
constructor, the run's state, and the seams between them.
`LoopManagerImpl` keeps its public face.

## Consequences

- A guideline release meets the engine in a handful of lines: one call in
  each root, a block at the end of each shared file, and the engine's own
  rows where the guideline lists its own, such as a refusal in the cloud.
- A layer above wires its own the same way, beside the engine's, and
  extends the loop through its hooks. A need the hooks do not meet is a
  new hook in the engine, never an edit of the loop in a copy.
- A test builds the engine as before: `build_managers` takes the engine's
  options as one object, `agent_root_options`.
