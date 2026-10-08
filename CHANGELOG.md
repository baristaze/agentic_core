# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.10.0 (2026-10-08)

The account mode runs whole under a hardened unit, and a transport
reads a file from an offset and ends a command at its own exit. Minor:
a transport an adopter wrote takes the offset, as Changed says; nothing
is reversed.

### Added

- The account mode's prepare runs under a unit with `ProcSubset=pid`
  and `RestrictSUIDSGID=yes`. Where the host shows
  `fs.protected_hardlinks`, it decides; where the unit hides it, the
  runner's `ACME_WORKSPACE_PROTECTED_HARDLINKS` declares it, off by
  default, so an undeclared hidden setting is refused. A workspace's
  home and tmp are `0o770` without the setgid bit, owned by the
  account's group, with a default ACL that gives that group what is made
  below them; a filesystem that keeps no ACL is refused at prepare
  (#71).
- `read_file` takes an offset in every transport and reads only what
  follows it, without following a link at any step, in host mode as in
  the account mode (#73).
- A command is over when its own process exits. Its output drains for
  two seconds; what still holds it is then ended (as the account, in
  the account mode), and the output is cut off after a grace. A child
  left holding the output no longer keeps a command to its deadline
  (#73, ADR 1023).
- A claude.ai run of `browser-judge-agentic` on 2026-10-08, with its
  permission mode at Auto, at sizes `m, m` on v0.9.0: 76 (#72).

### Changed

- For an adopter: `TransportInterface.read_file` takes `offset`
  (default 0), and a negative one raises `InfraValidationFailed`;
  `WorkspaceAccountImpl` takes `protected_hardlinks`, which
  `InfraConfiguredImpl` passes from the settings (#71, #73).
- ADR 1021 states the hardened unit's two settings (#71).
