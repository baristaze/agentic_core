# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.21.0 (2026-10-21)

The engine's base moves to the guideline's v0.63.0: a person signs in
through the identity provider alone, on every stack, and nothing keeps
a password. The demo plays as a scene in the browser. Minor, with one
reversal: the sign-in by an address alone is gone.

### Added

- `demo/dreamcatcher` plays the run as a scene in the browser: the
  person, the engine in our cloud, the model in its cloud, and the hands
  outside every cloud, a light on a wire for each step, the engine's
  rotor turning for each light it receives, and the numbers rolling like
  a meter. The page plays a tape `make demo-check` holds to the record,
  and the GIF is recorded from it (#121).
- From the base: `acme-api mint-local-login`, the local mint, which
  prints a sign-in token for each address from the local database and
  refuses at boot outside `local` and `test`, with no route and no page;
  the traffic generator mints a run's people with it (#122).

### Changed

- The base moves to the guideline's v0.63.0. Every stack signs in
  through WorkOS, the local one through the Staging application, and
  answers `503` there without its key. `make seed` makes each seeded
  person a plus-address of `SEED_EMAIL` in `.env` and refuses a domain
  that takes no mail; an existing checkout sets it and runs
  `make reset`. Every test suite signs in through the identity twin, the
  engine's own included, and the guideline links point at v0.63.0
  (#122).

### Removed

- From the base, the sign-in by an address alone:
  `POST /v1/auth/dev-sign-in`, `ACME_DEV_SIGN_IN_ENABLED`, the portal's
  `/login/dev`, and `--dev-email` (reversal) (#122).
