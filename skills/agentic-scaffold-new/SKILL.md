---
name: agentic-scaffold-new
description: "Start a new system on the engine: pick its name, copy the engine's scaffold under it, run its gates, record the product's first decisions in the thousand above its base's, then add its first agent kind."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(python3:*), Bash(make setup), Bash(make check), Bash(make infra-up), Bash(make migrate), Bash(make migrate-check), Bash(make test-integration), Bash(uv run:*), Bash(git status:*), Bash(git rev-parse:*), Bash(git show:*), Bash(lsof:*), Bash(docker info:*), Bash(date:*)
---

# agentic-scaffold-new

A path that starts with `../` is read from this skill's folder as
`realpath` resolves it.
Conventions: `../_shared/scaffold-conventions.md`.
Sections of `../../agentic_core_spec.md`: The Object Model, Agent Kinds
and Sub-Agents (Agent Kinds), The Repository (Being Adopted).

The scaffold, `../../scaffold/acme_root/`, is the guideline's scaffold
taken whole with the engine's namespaces in it: a system that runs,
green, with no product in it. This skill copies it under the product's
name, then adds the product: its first decisions and its first agent
kind. It writes no part of the core by hand. It is the guideline's
`arch-scaffold-new` over this scaffold, and differs where the engine's
layer does: the numbers of the product's decisions, and the first part
it adds.

## Input

`[<name>] [--first-kind <kind>] [--codeowners <owner,...>]`, and the
product: the path of its spec, or a description, in the arguments or in
the conversation.

Example: `help_desk --first-kind assistant`.

- `<name>` is the project's code name, its Python package, and its
  folder at once: one or two snake_case words, never a standard-library
  module, a Python keyword, or `acme`, and no longer than
  `MAX_NAME_LENGTH` in `../../scaffold/new.py` (17 characters), which
  keeps the tightest cloud name built from it within what AWS allows;
  `new.py` refuses a longer one and says the bound. With no name given,
  take the product's own short name when the spec or the description
  gives one. Else take the word of the product's name that says what
  the product is about, leaving out a word any product could carry,
  such as platform, app, agent, or system; two words only when one
  cannot say it. Ask only when the product names nothing.
- The folder is `<name>` in the current directory. It must not exist,
  or must be empty: an empty folder of that name is no collision, and
  the copy goes into it. Refuse when the current directory is inside a
  git repository (`git rev-parse --show-toplevel` answers there), since
  the copy starts a repository of its own.
- `--first-kind` names the product's first agent kind. With none, it is
  the kind of agent the product's first use runs, named for what it
  does, with the tools, the done rule, and the authority the product
  gives it.
- `--codeowners` names the owners `.github/CODEOWNERS` lists, comma
  separated, each an account or an `org/team`. Without it, the file
  keeps the copy's placeholder team, and the output names the file as
  one for the person to set.

## Created

| File | Holds |
|------|-------|
| `<name>/` | the scaffold, copied by `new.py` under the name in each of its forms, in a new git repository, keeping the guideline release the engine pins; from a clean checkout of this repository, its first commit is the copy, on `scaffold` and the main branch, the base `agentic-upgrade-scaffold` moves |
| `<name>/docs/adr/2001-*.md` and on | the product's first decisions |
| the first agent kind | as `agentic-scaffold-agent-kind` writes it |

## Changed

| File | Change |
|------|--------|
| `README.md`, `llms.txt` | the opening and the summary say what the product is, in place of the core's description |
| `.github/CODEOWNERS` (with `--codeowners`) | on every rule line, the owners in place of the placeholder team |

## Procedure

1. Settle the name and the folder, and refuse as the Input states.
2. Copy: `python3 <new.py> <name>`, where `<new.py>` is the absolute
   path of `../../scaffold/new.py`. Then, in the folder, `make setup`
   and `make check`. The copy is green before this skill writes
   anything, so a gate that fails here is a defect of the scaffold:
   stop with the cause pre-existing, name the gate, and change nothing.
   When `new.py` says the base is not recorded, the engine release the
   copy came from is the version the conventions read: the first move
   of the base grafts it.
3. Tell whether Docker runs: `docker info` exits 0 when it does. When
   it runs, and only then, settle the local ports before any stack
   starts: copy `.env.example` whole to `.env`, then check every port knob in it
   with `lsof -i :<port>`: the compose stack's and the host processes'
   (every `_PORT` knob the file holds). For each that is taken, set a
   free one in `.env`, with every URL knob that names it, and never
   stop what holds it. When Docker does not run, the database gates of
   step 6 are skipped, and the output names each one skipped.
4. Record the product's first decisions as ADRs, in the shape of the
   copy's own, each dated by `date +%F`. The copy's `docs/adr/` holds its base's records: the
   guideline's, from 0001, and the engine's, from 1001 to 1999. The
   product's own take the next thousand, numbered from 2001, so a later
   release of the base never brings the same number; never one above
   the highest record there.
   - One for the product on the engine: the engine release it builds
     on; what an org, a member, and an operator are in the product; its
     first agent kinds, who starts their sessions, and on whose
     authority they act; and the model providers it calls.
   - One per outside provider the product names beyond the identity
     provider and the model providers: the integration under
     `integrations/` that will reach it, and the twin that stands in
     wherever no account is configured.

   Then write the opening of `README.md` and the summary of `llms.txt`.
   With `--codeowners`, write the owners into `.github/CODEOWNERS`: on
   every line that is not a comment, the owners, each with a leading
   `@` and separated by a space, take the place of the placeholder
   team. The paths stay as they are.
5. Read `../agentic-scaffold-agent-kind/SKILL.md` and follow it with
   the first kind. Its gates, and those of any skill it follows, are
   not run there: step 6 runs them once, for all. A noun of the product
   beyond its agents is a namespace of its own, the guideline's
   `arch-scaffold-namespace`'s, after this skill.
6. Run the gates once: format as the conventions' After writing says,
   then `make check`, and when Docker runs, `make infra-up`,
   `make migrate`, `make migrate-check`, and `make test-integration`,
   as CI runs them on a copy.

A gate of steps 5 and 6 that fails on what this skill wrote is fixed,
and its step runs again from its first command, as After writing
states: the first run plus at most 3 reruns.

## Output

As `../_shared/scaffold-conventions.md` states, with these differences.
The name and where it came from come first. The copy is the two lines
`new.py` printed, its file count, the folder, the name, and the pin,
then its base, in place of its files: `git status` names what the copy
holds beyond its first commit, or every copied file when the base was
not recorded. After it, each file this skill and the skills it followed
wrote or changed, one per line; then the ADRs by number, and the
database gates skipped when Docker did not run. Then `What this skill
wrote is uncommitted, and its commit is the person's.` A `Stopped:`
line, when there is one, still closes the output.
