# ADR 1001: The base is the guideline's scaffold, taken whole

**Status**: accepted (2026-10-02)

## Context

The engine builds on the guideline's scaffold. It keeps Postgres
storage and the infrastructure impls, so nearly every module of the
scaffold is the engine's, and what is left is what a platform needs. A
cut would leave a platform a second base to take beside this one.

The guideline numbers its decision records from 0001, and each of its
releases can add one.

## Decision

The scaffold is taken whole. The repository's `scaffold` branch holds
the guideline's `scaffold/` folder unchanged, at the release the engine
builds on, and main merges it. Nothing is cut: storage, the
infrastructure impls, and their deployment postures stay. An adopter
drops what it does not need.

The engine's own decision records are numbered from 1001.

## Consequences

- The guideline's next release arrives by one merge, and a merge commit
  keeps the base, never a squash.
- A platform takes one base, the engine's scaffold, which holds the
  guideline's.
- A guideline record and an engine record never share a number, so a
  merge never renumbers either.
