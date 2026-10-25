# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.24.0 (2026-10-25)

The engine is measured on the path a deployment runs: `performance/`
replays a long session's shape with every step sealed. The loop no
longer opens again what it wrote, and a session's title is sealed like
the rest of its content. Minor, with one reversal: a read answers a
session's title sealed, and what shows it opens it.

### Added

- `performance/`, outside the scaffold: a long session's shape, kept as
  numbers and tool classes, replayed through the engine as the session
  runner wires it, every step sealed, on a Postgres per role, Valkey,
  MinIO, and the KMS adapter at a digest-pinned emulator or the key
  twin. A read pass pages each history and the session list as the API
  does, and a sampler ranks where the wall time goes. `make performance`
  runs it on its own compose project, and `make performance-check` runs
  a short replay in CI (#129).

### Changed

- A run holds what it reads and writes, for the run alone: its own
  appends join its history with no read back, an append opens nothing
  again, and recovering a lost run's calls reads on from the history
  read at its start. The key ring's revocation rides the statement that
  reads its versions. On the 1,500-turn replay with the KMS emulator,
  steps a second rise from 17.8 to 23.2, key calls for each step
  written fall from 4.41 to 2.86, and a run's start at its median from
  662 to 383 ms (#131).
- A session's title is content, sealed at rest under a title key of its
  own, wrapped under the tenant's title key, which the key service
  wraps (ADR 1042). A page of titles opens with one read of its title
  keys and one unwrap. A revocation clears the title key in its commit,
  so the title reads absent while the session keeps its place; a
  rotation rewraps the tenant's key, and a tenant's purge takes it. A
  memory-only session's title is kept at rest, sealed. This reverses
  the earlier contract: a read, `create_agent_session` included,
  answers a title sealed, and what shows one opens it with
  `open_titles`, as the API, the sub-agent tools, and the demo do
  (#132).
- The core migration `202610250340` only adds: a row this release
  writes holds `(sealed)` in the column the release before reads, and
  the maintenance sweep seals each existing title in place, never
  dropping one (#132).
