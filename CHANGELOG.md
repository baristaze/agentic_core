# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.2.0 (2026-10-03)

Three tools the engine ships for any agent kind that lists them. Minor:
rules are added, and no released rule is reversed.

### Added

- `ask_person` parks the loop on `person` before any further model call,
  its question kept in the history, and the person's answer resumes it as
  the next input; an answer that lands before the park's status is
  projected still asks for the run (#23).
- `write_plan` keeps the agent's plan by version, rendered last in the
  window on the next call and read by a person through the steps (#23).
- `read_attachment` reads a session's attachment by line or page range,
  bounded by its answer's serialized size, never another session's or
  tenant's; the session runner takes a product's attachment reader from
  its entry point (#23).
