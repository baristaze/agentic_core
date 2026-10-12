# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.17.0 (2026-10-12)

Every state a person can reach has a known way out. A cancel ends a
loop parked on a person, a job, or a line; every park a person clears
has a route that clears it and wakes the session; a loop's end and its
park name their cause. A session is org-wide or private, and one
steerer instructs it. A budget closes once and a closed one binds
nothing. The engine's base is the guideline at v0.60.0. Minor, with
reversals named: a session's history no longer opens to every member
and a participant's message no longer instructs it (#108); a hand-over
is unlocked by its holder alone, in person, and a job's completion
takes no cost over the wire (#106); an amount change no longer reopens
a closed budget (#104).

### Fixed

- A sub-agent's report to a parent whose key is revoked is dropped, as
  for a parent that is gone, and its loop ends; it raised `KeyRevoked`
  and held its tree and its run open. A report over the bound is
  dropped the same way (#101).
- Under `SystemCallFilter=~@privileged`, the account mode's prepare
  failed on a bare `PermissionError`, or waited ten seconds and raised
  `ProcessesOutlived`. It tries the switch's calls and a chown first and
  refuses with `IsolationRefused` naming `SystemCallFilter` and the sets
  a unit allows beside it: `@setuid`, `capset`, and `@chown` (ADR 1021
  amended) (#103).
- A deadline with no offset is refused with 422, not a 500. A sub-agent
  that held a lapsed principal runs again once its root is assigned
  (#106).

### Added

- The spec's Always in a Known State section says once what the core
  rules add up to: every state a session reaches is named, keeps every
  rule, recovers, and has a way out. No rule or term is added (#102).
- `LoopEndedHeader` and `Park` take an optional `cause`, cut to 500
  characters. `IsolationRefused` carries one beside its log message,
  composed from the spec alone: a limit a provider cannot meet, a base
  being built, a setup command and its exit code. The loop writes it on
  its `errored` end and its resource park; the API returns `cause` on a
  `loop_ended` step and `park.cause` on a step and a session; the CLI's
  history line shows it (ADR 1035, lenses TOL-14, TOL-16, BND-09,
  STP-08) (#105).
- Routes that clear a person's park: `PUT .../deadline`,
  `PUT .../principal`, `POST .../take-over`, `POST .../give-back`, and
  `POST .../jobs/{key}/completion`, each held to its manager's
  permission. An assignment wakes its park, and on a root its
  sub-agents' that held its principal. A hand-over names its holder
  (ADR 1039) (#106).
- A session is org-wide or private: steady kinds start org-wide,
  delegated kinds private, and its creator switches it, a recorded step.
  History and artifacts open to every member when org-wide, else to the
  creator, participants, and `manage_sessions`; others get 404.
  `PUT .../principal` with `{"steerer"}` hands the steer to a
  participant, and with no body `manage_sessions` takes a lapsed one.
  Routes: list, participants, visibility, artifacts (ADR 1038,
  lenses PRV-01, TRU-05) (#108).
- A test cancels a loop parked on a person, on a job, and in line, and
  the spec says what a second start does while a session is open (#107).

### Changed

- `close_budget` closes a budget once, through the version's
  compare-and-set. The gate reads open budgets alone, on an index
  holding only them, so a closed budget binds no call and never counts
  toward the budgets one call may read. A budget counts from its
  creation, so one made on a session's tree before a run caps that run
  alone. A close wakes the sessions whose park names it;
  `wake_parked` and `WakeSessionsPayload` take an optional `unlock`
  (ADR 1036, lenses BND-01, BND-07). Migration `202610121517` on `core`
  (#104).
- A `billing` or `credential` park retries after five minutes, and its
  cause says who must act. `receive` refuses a principal's unlock of a
  hand-over another holds (#106).
- Only the steerer instructs a session. A participant's message is data
  with its author, never waking or paying. `manage_sessions` is the
  service role's too, so a run reads its session. Migration
  `202610121725` on `core` (#108).
- The base moves to the guideline at v0.60.0: the orchestrations
  `cancel`, `cancelled` among the settled statuses, and ADR 0099. The
  spec and the lenses cite v0.60.0 (#107).

### Removed

- A hand-over's unlock by anyone who may write, API keys included: only
  its holder, in person, gives it back or unlocks it, and a cancel
  still ends it. A reversal (#106).
- A job's completion taking a cost over the wire. A reversal (#106).
- A session's history and artifacts open to every member, and a
  participant's message instructing the session. A reversal (#108).
- An amount change reopening a closed budget. A reversal (#104).
