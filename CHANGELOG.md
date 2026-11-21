# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.26.0 (2026-11-21)

A kind is a folder: a Python module at its root holds its typed
settings, and Markdown holds its prompts, each released version's
pinned by its digest. Who may start a kind is a field of its own.
Minor, with one reversal: a spawn starts only a kind whose
`started_by` names `agent`, and a kind that says nothing is started by
a person alone, so a layer's kinds that a spawn starts name `agent`.

### Added

- `AgentKind.started_by`: a person, an agent, or both may start a kind,
  a person alone by default. A start and a hand-off need a person, a
  spawn needs an agent, and each is refused before anything is
  written. A request kind that names `agent` is refused at boot
  (ADR 1046, #139).
- A check of each kind's prompts: a prompt changed under a released
  version, a referenced file missing, a prompt file no kind references,
  and a prompt past its bound are each refused, naming the file
  (lens AGT-09, #139).

### Changed

- Each kind is a folder of its own, `kinds/<kind>/`: `kind.py` holds
  its typed `AgentKind` constants, one per version, with `VERSIONS`,
  and `prompts/v<N>.md` holds each version's prompt, read at import
  with `prompt_of`. `prompts/lock.json` pins each released prompt's
  digest; an entry is never changed or removed. A layer above may add
  folders of its own beside them, which the engine ignores. The example
  assistant moved to its folder, and the agent-kind and tool skills
  write a kind that way (ADR 1046, #139).
