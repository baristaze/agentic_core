# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.27.0 (2026-11-21)

A layer's loop hook may refuse a session's workspace before the first
model call, and the loop ends naming the refusal's cause. Minor.

### Added

- `before_first_call` may raise the session's workspace refusal
  (`IsolationRefused`). The run stops before any model call, with
  nothing prepared, as on a refused spec: a refusal that cannot clear
  ends the loop `errored`, its cause on the step that ends it, and one
  that clears parks the loop on the resource. The spec's hooks
  paragraph, the hooks' contract, and ADRs 1041 and 1043 say so (#143).
