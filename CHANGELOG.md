# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.11.0 (2026-10-08)

The engine's base moves to the guideline at v0.53.0, which makes a
feature flag an infra capability behind `FlagsInterface`, with its
provider chosen at boot, and gives a client its session's flags as one
snapshot from `GET /v1/flags`. Minor: the base carries the guideline's
one reversal, DEL-22; the engine's own rules reverse nothing.

### Added

- From the guideline's v0.53.0, in the scaffold: `FlagsInterface` in
  infra beside the engine's keys and runtime, built last and closed
  first; `ACME_FLAGS_BACKEND` (`memory`, `launchdarkly` through
  OpenFeature, or `none`); `media-uploads` gating a new upload with
  `403 feature_off`; `GET /v1/flags` with its `ETag`; the portal's
  snapshot and its paused notice; ADR 0085 (#75).

### Changed

- The spec's links and the lenses' README cite the guideline at v0.53.0
  (#75).
- From the guideline: DEL-22, reversed. A vendor's flag SDK outside the
  infra flags package is the violation, and DEL-52 keeps one out of a
  browser app (#75).
