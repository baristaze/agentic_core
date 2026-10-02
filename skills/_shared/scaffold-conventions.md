# Scaffold conventions

Every `agentic-scaffold-*` skill follows these. A skill's own file says
what it adds: its input, the files it creates and changes, and the
steps that differ.

## The shape is a file

The engine's scaffold is the guideline's, taken whole, with the
engine's namespaces in it. Every file a skill writes has a sibling that
shows its shape. In a tree copied from the scaffold, the sibling is the
tree's own file at the path the skill names. When the tree has no such
file, it is the plugin's copy, `../../scaffold/acme_root/<path>` from
the skill's folder, with `acme` read as the tree's name. Read the
sibling, its imports, and its test before writing. Copy its shape, not
its domain.

A skill states only what an agent gets wrong from the files alone. The
lens group it names says the rest: each Violation in
`../../lenses/<group>.md` is a way a copy of the shape still goes
wrong.

`<name>` is the tree's name, the folder under `om/src/`, in the forms
`../../scaffold/new.py` gives it: snake case for the package and the
paths, kebab case for distributions and cloud resources, the upper
snake prefix for the environment, and Title case for prose. Never
assume `acme` in a copy.

A part in the guideline's own shape, such as a namespace, an entity, a
kind of work, or a setting, is the guideline's to scaffold: its
`arch-scaffold-*` skills and their conventions.

## Before writing anything

1. Read `version` in `../../.claude-plugin/plugin.json`, from the
   skill's folder as `realpath` resolves it, and name it in the output
   as "agentic_core `<version>`, or a later snapshot of main".
2. Read the sections of `../../agentic_core_spec.md` and the lens group
   the skill names, then the sibling files its steps name.
3. Check every path the skill creates. A file that exists is a
   collision: stop and say so, and never overwrite. An empty folder is
   no collision; the skill writes into it.
4. Ask for everything the input lacks in one message, then proceed.
   When no one answers, as in an unattended run, decide each from the
   product's spec or description and the sibling files, name each
   choice in the output, and go on.

## After writing

Before the first gate, format what was written with the tree's
formatter, `uv run ruff check --fix .` and then `uv run ruff format .`.
A formatter run is no gate run, and no count holds it.

1. Run the tree's fast gate, `make check`, at its root. A gate that
   fails on what the skill wrote is fixed, and the gate runs again: the
   first run plus at most 3 reruns. When the count runs out, stop, and
   leave the tree as the last run left it. A fix edits only files the
   skill created or changed.

   Three failures stop at once, with no fix: one that was there before
   the skill ran; one of the machine (no network, a tool missing); and
   one whose fix takes an exception to a rule of the spec or the
   guideline, or removes, skips, or suppresses a test. That fix is the
   person's decision, recorded as an ADR (`agentic-deviate`).
2. Print the version, then every file created or changed, one per line,
   from `git status --porcelain --untracked-files=all`, then each choice
   made without an answer, then each command run, once, with the outcome
   of its last run. A stop closes the output with
   `Stopped: <command>: <what went wrong>; <cause>`, the cause one of:
   the count ran out, pre-existing, the machine, needs an exception.

Never commit. A scaffold leaves a working tree for a person to review.
