# agentic_core

The engine under an agent: a tool-call loop with a model as its
decision-maker, made durable, bounded, steerable, private, and
auditable.

The engine adopts the [Software Design and Architecture
Guidelines](https://github.com/baristaze/swe_guidelines), *the
guideline*, for every question of general software design, and repeats
none of it. This repository ships in the guideline's own shape: a spec
that tells the story, and tools that hold the detail.

- **[`agentic_core_spec.md`](agentic_core_spec.md)**: the spec. It says
  what the engine is, what it guarantees, and why. Start with its
  [Core](agentic_core_spec.md#the-core).
- **`lenses/`**: the checkable detail under each rule of the spec, one
  file per group, in the guideline's lens format.
- **`skills/`**: skills named `agentic-*` that review a change through
  the lenses, explain a rule, record a deviation, and scaffold the
  engine's parts. They follow the [Agent
  Skills](https://agentskills.io/specification) standard.
- **`scaffold/`**: the engine's domain-free core, built on the
  guideline's scaffold. A platform renders it under its own name. It
  carries `checkers/`, the checker for the lenses a program can decide,
  so every copy runs it.

[The Repository](agentic_core_spec.md#the-repository) in the spec says
what each part holds. A part's folder appears with the change that
builds it, and `make check` already holds the folder to its gate.

## How this repository is written

The spec and the READMEs have two readers, a person and an agent. They
tell the story: what each part is, and why. They point at the detail
rather than spell it out. Nuance that only an agent needs sits in a
short `<!-- agents-only -->` comment, which a rendered page hides. The
lenses and the skills are read by agents. They may be exact, and they
point at the scaffold's files rather than describe code.

The detail lives in the tools, and that is a choice. The spec tells the
story. The lenses and the skills hold the detail under each rule, and
they are stricter than the story on purpose. The checker and the gates
are stricter still. A tool may be stricter than the section it cites,
and never contrary to it.

## Install the skills

In Claude Code, the repository is a plugin marketplace:

```text
/plugin marketplace add baristaze/agentic_core
/plugin install agentic-core@agentic-core
```

## Develop

```bash
make check       # everything CI runs
make gen-toc     # regenerate the spec's Contents from its headings
make gen-skills  # regenerate the review skills from the template and the lenses
```

[`CONTRIBUTING.md`](CONTRIBUTING.md) says what `make check` needs and
how a change lands. [`AGENTS.md`](AGENTS.md) holds the layout and the
writing rules.

## License

Apache 2.0. See [`LICENSE`](LICENSE).
