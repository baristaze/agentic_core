# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.9.0 (2026-10-07)

The engine holds the seams a platform built inside its loop, and a
directory on a host can run its commands as an account of its own.
Minor: an adopter's gate, sink, and loop wiring change, as Changed
says; nothing is reversed.

### Added

- Each model call asks `CallCredentialsInterface` for its client and
  key, in the loop and in a compaction. The engine's own answer is the
  platform's key; a root's models layer supplies another. A call whose
  key cannot be had parks on its provider, with unlock
  `<provider>:key`, and spends nothing; a key its provider refuses is
  marked refused, and no outage is reported (#66).
- A gate that raises `GateParked` parks the loop where it says, and
  `SpenderUnknown` parks on a person, on the model-call path and the
  job path (#66).
- A stream sink is told `opened` and `completed` around a model stream
  and a tool call, also when the call fails (#66).
- A workspace refusal that says it clears parks the loop on the
  resource and asks again; any other ends the loop `errored`, as
  before. A lost workspace parks on a person, the park unsettled, and
  ADR 1009 says how its calls settle (#66).
- The account mode (`IsolationMode.ACCOUNT`, ADR 1021): a directory on
  a host whose commands run as one named account, with no
  supplementary group, no capability, `no_new_privs`, and a process
  limit when asked. The account serves one workspace at a time. Its
  release ends every process of the account as the account, so a
  hidden `/proc` hides none of them; its purge removes the directory
  without following a link; its transport refuses a file of the runner
  with a second link; and it refuses a host without protected hard
  links (#67).
- A claude.ai run of `browser-judge-agentic` on 2026-10-07, at sizes
  `m, m` on v0.8.0: 77 (#69).

### Changed

- For an adopter: `CallGateInterface.authorize` takes `credential`;
  `LoopManagerImpl` and `WindowsManagerImpl` take the credentials in
  place of the providers registry; a `StreamSinkInterface` implements
  `opened` and `completed`; `IsolationRefused` takes `clears` (#66).
- `agentic-benchmark-browser` polls a site for up to three hours, 180
  polls, and the schema's `polls` follows (#68).
- The spec names the layers above the engine only in its Next section
  (#65).
