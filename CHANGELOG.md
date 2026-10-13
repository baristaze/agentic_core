# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.18.0 (2026-10-13)

An audit read is not a membership. A member joins only an org-wide
session on their own, and a private session's participants are the ones
its creator adds. A route removes a participant: its creator removes
anyone, and a participant leaves on their own. Minor, with no reversal.

### Fixed

- A holder of `manage_sessions` joined a member's private session on
  their own, a membership its creator never gave that outlived the
  permission. A self-join takes an org-wide session alone: on a private
  one it gets 403, `manage_sessions` included, and so does an add by
  anyone but its creator. Who takes a lapsed steer joins once
  the move is recorded, the one way into a private session its creator
  did not give. Self-joins made before this release stay until the
  creator removes them (#110).

### Added

- `DELETE /v1/agent-sessions/{session_id}/participants/{user_id}`
  removes a participant: its creator removes anyone, and a participant
  leaves on their own. Anyone else gets 403, and the steerer 409 until
  the steer is handed on. Removed from a private session, a person
  without `manage_sessions` no longer sees it. The OpenAPI document and
  the TypeScript client's schema carry the route (#110).

### Changed

- The spec's Who Is Who says who joins, how a participant is removed,
  that the steerer is removed only once they hand the steer on, and
  that a switch to private keeps who takes part. Lens PRV-01 looks for
  the checks that add and remove a participant, and holds a self-made
  participant of a private session and a participant no route removes
  as violations; TRU-04 holds a steerer removed while they steer (#110).
