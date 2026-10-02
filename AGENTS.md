# Working in this repository

This repository holds one spec (`agentic_core_spec.md`), the parts that
make its rules checkable and runnable (`lenses/`, `skills/`, `agents/`,
`scaffold/`, `checkers/`), and the scripts that keep them consistent
(`scripts/`, `Makefile`). It adopts the [Software Design and
Architecture Guidelines](https://github.com/baristaze/swe_guidelines),
*the guideline*, and has its shape. Each script's docstring states what
it holds. Read it before you change what it checks.

## The ladder

The spec tells the story and states every rule of the engine. The
guideline states every rule of general software design, and the spec
cites it rather than repeat it. A lens or a skill holds the checkable
detail under a rule the spec states. It is stricter than the story on
purpose, never contrary to it, and it never adds a rule the spec does
not state. The checker and the gates are stricter still. When the spec
changes, every lens and skill that cites the changed text changes with
it.

## Writing

- A person and an agent read the spec, the READMEs, and `docs/`. They
  tell the story and point at the detail: short sentences, one idea
  each, in the present tense, with no history.
- Only agents read the lenses, the skills, and `agents/`. They may be
  exact, and they name the scaffold file whose shape to follow.
- Detail goes where it is held: code to the scaffold, checkable detail
  to a lens, steps and edge cases to a skill, a stance and its reasons
  to an ADR. What only an agent needs on a person's page goes in a
  short agents-only block, opened by `<!-- agents-only` alone on its
  line; nothing inside may close it. Tags follow How to Read This, in
  the spec.
- The spec borrows a product's nouns in its *Example* lines only, to
  illustrate. Nothing else here carries product or hardware vocabulary,
  and nothing outside the spec names a repository but the guideline.

## Layout

- `agentic_core_spec.md` is the source of truth. Its Contents lists
  every section that follows it; it is generated (`make gen-toc`) and
  checked (`make toc`). Headings are unnumbered, and every
  cross-reference names a section by its title. A rule of the guideline
  is cited by a link pinned at the release the engine builds on.
- `lenses/<group>.md` holds one group of lenses in the guideline's lens
  format. The spec's The Repository names the groups.
- `skills/agentic-<name>/SKILL.md` is a skill of this plugin. Its
  `name` is its folder's and starts with `agentic-`, so it never
  collides with the guideline's `arch-*`. `scripts/check_skills.py`
  holds its frontmatter and the paths it names.
- `agents/agentic-<name>.md` is a Claude Code subagent a skill fans out
  to. `scripts/check_agents.py` holds its frontmatter and its
  `maxTurns`.
- `scaffold/acme_root/` is the engine's domain-free core under the name
  `acme`, built on the guideline's scaffold. Its ADRs are numbered from
  1001, so they never meet the guideline's. Its skills sit in
  `.agents/skills/`, and its `.claude/skills` is a link to them;
  `scripts/check_skills.py` holds their frontmatter and the link.
- `checkers/` holds the checker for the lenses a program can decide.
- `.claude-plugin/` holds the plugin and marketplace manifests; the
  repository root is the plugin, `agentic-core`. `plugin.json` carries
  the one release version, and `scripts/check_version.py` holds every
  copy to it.
- `scripts/_common.py` holds what the scripts share: the heading anchor
  rule, the one list of the repository's Markdown (`markdown_files`),
  the argument parser, and the fence rule (`fenced_lines`).
- `tests/` holds one pytest module per script, with a pass and a fail
  path per rule; `make test` runs them.

A gate over a folder that does not exist passes on the empty set, so
each part is held from the change that adds it.

## Invariants

- Every skill follows the [Agent Skills
  standard](https://agentskills.io/specification), so it runs in any
  agent that reads it. Its frontmatter holds the standard's fields and
  nothing else, except `disable-model-invocation`: Claude Code's key,
  which the standard's validator refuses. So only a skill a person must
  start by name carries it, and that skill also carries Codex's switch,
  `agents/openai.yaml` with `policy.allow_implicit_invocation: false`.
- A skill names its own files by a path from its own folder
  (`../../agentic_core_spec.md`, `references/<file>`), never through a
  path one agent substitutes, such as `${CLAUDE_SKILL_DIR}`. A skill
  that climbs out of its folder says the path is read from the folder
  as `realpath` resolves it. Its arguments are "the arguments", never
  `$ARGUMENTS`.
- A skill's `allowed-tools` names only what its body runs.
  `scripts/check_skills.py` holds the frontmatter, the make targets, and
  the paths; the git, uv, and pnpm entries are held by hand.
- "X, never Y" names the near miss a rule rules out. It is part of the
  rule, not history.

## Validate

```bash
make check                          # everything CI runs
claude plugin validate . --strict   # the manifests (when claude is installed)
```

`.github/pins/` holds every tool version the Makefile and CI run.

## Conventions

- Prose wraps at about 72 columns. A concept the spec uses before the
  section that defines it links that section at its first mention.
- A fix applied to one instance is searched for its siblings, and they
  are fixed in the same change.
- A commit message has a specific subject and a short body naming the
  rule that changed and why.
- A change never edits `CHANGELOG.md`. Its pull request description
  carries what the release needs: what changed, the level
  `CONTRIBUTING.md` (Versioning) gives it, and a reversal named as one.
