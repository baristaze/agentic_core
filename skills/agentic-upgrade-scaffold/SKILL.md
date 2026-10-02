---
name: agentic-upgrade-scaffold
description: "Move a platform's base to a later engine release: a product rendered from the engine's scaffold, or a layer that takes its scaffold/ folder unchanged. Runs the guideline's arch-upgrade-scaffold with the engine as its source, so one move takes both foundations, and adds what the engine's layer holds: its releases, its ADR numbers, its migrations, and a layer's gates in a copy."
allowed-tools: Read, Grep, Glob, Edit, Write, Bash(curl:*), Bash(gh api:*), Bash(gh release view:*), Bash(python3:*), Bash(git status:*), Bash(git fetch:*), Bash(git rev-parse:*), Bash(git symbolic-ref:*), Bash(git switch:*), Bash(git log:*), Bash(git show:*), Bash(git diff:*), Bash(git merge:*), Bash(git merge-base:*), Bash(git checkout:*), Bash(git rm:*), Bash(git add:*), Bash(git commit:*), Bash(git ls-files:*), Bash(git ls-tree:*), Bash(grep:*), Bash(uv lock:*), Bash(pnpm install:*), Bash(docker info:*), Bash(lsof:*), Bash(make setup), Bash(make check), Bash(make openapi), Bash(make infra-up), Bash(make migrate), Bash(make migrate-check), Bash(make test-integration)
---

# agentic-upgrade-scaffold

A path that starts with `../` is read from this skill's folder as
`realpath` resolves it.
Sections of `../../agentic_core_spec.md`: The Repository (Adopting the
Guideline, Being Adopted).

A platform's base is one render of the engine's scaffold, which holds
the guideline's at the release the engine pins. A product renders it at
its root, under its own name. A layer takes the engine's `scaffold/`
folder unchanged. The guideline's `arch-upgrade-scaffold` makes the
move for both, with the engine as its source, so one move takes both
foundations. This skill holds only what the engine's layer adds.

## The procedure

`<pin>` is the guideline release this plugin's scaffold pins: `pinned
at release` in `../../scaffold/acme_root/specs/architecture.md`. Read
the guideline's skill at that release,
`curl -fsSL https://raw.githubusercontent.com/baristaze/swe_guidelines/v<pin>/skills/arch-upgrade-scaffold/SKILL.md`,
and follow it, with the differences below. Its `<base.py>` is the
absolute path of this plugin's `../../scaffold/base.py`, the same
script. Every other path it names from its own folder is the
guideline's file at `v<pin>`, read from
`https://raw.githubusercontent.com/baristaze/swe_guidelines/v<pin>/<path>`.

## Input

`[<ref>] [--name <name> | --layer] [--from <ref>] [--tarball <file>]`,
read as the guideline's skill reads them, with these differences.

- The source is the engine, `https://github.com/baristaze/agentic_core`,
  and every run of `base.py` carries
  `--source https://github.com/baristaze/agentic_core` among its
  `<flags>`. The repository is private: `base.py` reads it with the
  token `gh auth token` gives, and `--tarball` serves a machine with
  none.
- `<ref>` is a release of the engine, never of the guideline, whose
  release the pin names. Without one, it is the release after the one
  the base records. The engine's releases and their commits:
  `gh api --paginate repos/baristaze/agentic_core/tags --jq '.[] | "\(.commit.sha) \(.name)"'`,
  read in version order, so `v0.10.0` follows `v0.9.0`. The base's
  release is the tag whose commit is its `Scaffold-Commit`, and the
  target is the next. A move takes one engine release: when releases
  lie between the base's and a target named, the target is the first of
  them. When the base's commit is no release, `<ref>` is required. When
  no release is above the base's, stop: there is nothing newer. A
  layer's first take has no base, and without `<ref>` takes the newest
  engine release in that listing, never a guideline tag.
- `--from <ref>`: the engine release a checkout with no base was copied
  from. `agentic-scaffold-new` names it in its output and in the
  product's first decision.

## What the engine's layer adds

- **The graft** (its step 3). A checkout with no base grafts at the
  engine release it was copied from, never at its pin:
  `python3 <base.py> <from> <flags>`, then the merge that changes no
  file, as that step gives it. Without `--from`, stop and ask for it. A
  layer's first take grafts nothing, as there.
- **What the releases ask** (its step 5). The engine's notes at the
  target:
  `gh api -H 'Accept: application/vnd.github.raw' 'repos/baristaze/agentic_core/contents/CHANGELOG.md?ref=<ref>'`.
  When the target pins a later guideline release than the checkout does
  (`pinned at release` in `<root>/specs/architecture.md`, before and
  after the merge), also read the notes of each guideline release above
  the checkout's pin up to the target's,
  `gh release view v<X.Y.Z> --repo baristaze/swe_guidelines`. One move
  takes both foundations, and a deviation the checkout records may be
  one a guideline release now holds.
- **ADR numbers** (its step 6, `docs/adr/`). The base's records come in
  as the scaffold's: the guideline's, from 0001, and the engine's, from
  1001 to 1999. The checkout's own sit in the thousand above its base's:
  from 2001 on the engine, and in the thousand above a layer's own on a
  layer built on it.
- **Migrations** (its step 6). The engine's migrations sit in the
  guideline's four role chains, after the guideline's, and they are the
  scaffold's, as the guideline's own are. A role's scaffold head is the
  render's: the engine's last migration of that role where it has one,
  else the guideline's.
- **A layer's gates** (its step 8). A layer runs its scaffold's gates in
  a copy, as the engine's CI does, never in `<root>`. From the
  checkout's root, `python3 scaffold/new.py <dir>/<name>` copies it into
  a folder outside the checkout. In the copy, `make setup` and
  `make check`; when `docker info` exits 0, `.env.example` copied whole
  to `.env`, each port in it that `lsof -i :<port>` finds taken moved to
  a free one, then `make infra-up`, `make migrate`, `make migrate-check`,
  and `make test-integration`. `make openapi`, when its step asks for
  it, still runs in `<root>`, where what it writes is committed. The
  layer's own gates, as its `AGENTS.md` names them, run at its root. A
  fix goes into the checkout, never the copy, and a fresh copy runs the
  gates again.

## Output

As the guideline's skill gives it, with the engine release of the base
before and after, and the guideline release each pins.
