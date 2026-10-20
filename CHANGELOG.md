# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.20.0 (2026-10-20)

The engine's base moves to the guideline's v0.62.0, whose retry waits
and periodic loops come from one shared package, and the engine's own
retry wait takes its curve from it. A model call that never left
releases its hold, a failed control read leaves a running tool call
running, and a demo runs the engine end to end. Minor: the base's
folder tree names `libs/`.

### Added

- `demo/dreamcatcher` runs the engine in process on the scripted twin,
  on one terminal screen, through three acts: one loop; a steer, a
  question, and two sub-agents; a budget park and a runner replaced mid
  tool call. `make demo` draws it, `make demo-check` checks each act
  from the record in CI, and its GIF tops the README (#117).
- From the base: `libs/timing` (`acme-timing`), with `RetryPolicy`,
  `Retries`, `retry_after`, and `Every`, and the TypeScript client's
  `RetryPolicy` and `Retries` (#118).

### Changed

- The base moves to the guideline's v0.62.0: its outbox, work, sign-in,
  client, and listener waits come from `RetryPolicy`, and the worker's
  heartbeat, sweep, and tally run on `Every`, start to start. The
  engine's sweep runs its own pass on `Every`, and the guideline links
  point at v0.62.0 (#118).
- The model loop's retry wait comes from `RetryPolicy`, on the same
  curve, with a provider's Retry-After still its floor, so a long one
  parks the run. The adapters read `retry-after-ms`, then `retry-after`
  through `acme.timing.retry_after`; a value that is neither whole
  seconds nor a date is no ask (#119).

### Fixed

- A model call whose request never left fails with no partial reply,
  so its hold is released (#116).
- A failed control read leaves a running tool call running (#116).
- From the base: the sign-in delay caps its doublings, a failed
  delivery comes back later on each receive, and the storage-bounds
  tests open their connections under the checkout bound (#118).
