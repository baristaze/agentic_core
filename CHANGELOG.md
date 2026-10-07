# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.8.0 (2026-10-07)

The engine runs its own browser benchmark with a skill, and its base is
the guideline at v0.52.2. Minor: the plugin gains a skill, and nothing
is reversed.

### Added

- `agentic-benchmark-browser`, the guideline's browser benchmark
  carried into the engine's plugin and bound to `browser-judge-agentic`.
  Its prompt names the repository's public URL, and the whole repository
  is judged; nothing is attached. A session whose page names a device
  of the person's, at any point, is stopped and not run. The check-in
  holds `results.json` to the schema and redacts conversation
  addresses, people's and devices' names, the person's other
  repositories, and any name on a list the person keeps outside the
  repository.
- Run 2 of `browser-judge-agentic`, on 2026-10-07 at sizes `m, m`:
  chatgpt.com 89 and grok.com 86; gemini.google.com declined, with no
  access to the URL, and claude.ai was not run.

### Changed

- The base moves to the guideline at v0.52.2: a copy's access-line test
  bounds the line by the relay's measured start, not a fixed 300 ms.
  The spec and the lenses cite the guideline at v0.52.2.
