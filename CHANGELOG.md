# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.1.0 (2026-10-02)

The engine's first release: its spec, the lenses and skills that apply
it, the checker for the lenses a program can decide, and its scaffold,
the guideline's at v0.48.0 taken whole, with the engine built in.
Minor: every rule is added, and no released rule is reversed.

### Added

- The repository has the guideline's shape: its gates, its CI, its
  plugin `agentic-core`, and the documents an agent reads (#1).
- The engine's rules are lenses, in nine groups a review reads one at
  a time (#2).
- A review skill per lens group, a full review that runs them all, and
  skills that record a deviation and explain a rule (#3).
- The scaffold is the guideline's at v0.48.0, taken whole and merged
  from the `scaffold` branch, and its stack runs on the engine's own
  ports (#4).
- Steps and sessions are stored durably and append-only, and a writer
  epoch fences who writes them (#5).
- A call names a model role: fills, fill sets, explicit switches, the
  provider boundary, and two adapters with a scripted twin (#6).
- Every model call and spending job passes one gate before it starts.
  A limit parks or ends a loop, and a park names its unlock (#7).
- Step content is sealed per session under a versioned key. Revoking
  the key erases it, and a session may keep its content in memory
  only (#8).
- Actor, principal, and spender are three answers, only a principal
  instructs, and agent kinds, sub-agents, and handoffs share one
  loop (#9).
- A session's history is purged by a login of its own, after its
  retention or its org's deletion, and is marked deleted before
  that (#10).
- A model request renders deterministically from the pinned zone and a
  window sized for its model. Compaction changes what is read, never
  what is kept (#11).
- Tools declare their class and effect, policy decides by class and
  target, and the runtime is injected and refuses weaker
  isolation (#12).
- Scaffold skills add a tool, an agent kind, a provider adapter, and a
  model role, start a system on the engine, and move a platform's
  base (#13).
- `agentic-check` decides the lenses a program can, in a project's
  fast gate, and every lens points at its shape in the scaffold (#14).
- The loop runs over the engine's namespaces: it persists before it
  proceeds, recovers by effect, steers, and streams, and every
  interface has its null object (#15).
- A full review runs unattended: it lists a scope's files, reads a path
  from the working tree, and merges findings by one rule (#16).
- A session runner claims each woken session's loop, the API and the
  command line start, steer, and read a session, and a loop runs end
  to end across a crash (#17).
- A file marks its session, a child acts under its inherited
  principal, and an instruction needs every permission its registry's
  classes need (#18).
- A malformed tool use never runs, an output past its bound keeps each
  stream's head and tail, a repeatable call is retried once, and a
  workspace's stragglers end with it (#19).
- A transport's record keeps a command's output sealed under its
  session's key and goes with its purge, and a tool input's hash is
  keyed by its session (#20).
