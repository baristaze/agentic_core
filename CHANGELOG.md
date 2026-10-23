# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.23.0 (2026-10-23)

The engine's base moves to the guideline's v0.65.0: a call whose refusal
costs its caller what it holds goes through under load, and every blob
crosses by a presigned URL, as a multipart upload that resumes. Minor,
with the guideline's reversals: the presigned POST and the presign that
may answer `None` go, and CTX-07 allows a second context variable.

### Added

- A holder's calls, from the guideline's v0.65.0: a route marked
  `HOLDER_CALL` is counted in a `holds` lane of its own and a
  per-credential budget of its own, and takes a reserve beside a full
  database pool (ADRs 0106 and 0108). A job's completion is one, since a
  job times out at its deadline and a completion shed with the writes
  loses its work. The storage root keeps the engine's purge login beside
  the reserves, and the purge pool opens none; the connection budget
  counts both (#127).
- Multipart transfers in Buckets, from the guideline's v0.65.0: an upload
  that stops resumes by its id, sending only the parts it still misses;
  the Python client, the CLI (`--resume ID`), and the TypeScript client's
  `uploadFile` send parts straight to the store (ADR 0107) (#127).

### Changed

- A blob crosses only by a presigned URL, at any size, as a multipart
  upload, and arch-check refuses a presign that may answer `None`: both
  reverse the guideline's earlier contract. CTX-07 allows a second
  context variable, the holder's call's mark, a reversal in part. The
  engine's core migration `202610150049` takes the guideline's
  `202610221532` as its parent. The spec and the lens index link the
  guideline at v0.65.0 (#127).

### Removed

- `presign_post`, and the media route that served a file's content
  through the API (#127).

### Fixed

- A copy's first commit and a render hold only what the scaffold's
  `.gitignore` files keep (#127).
