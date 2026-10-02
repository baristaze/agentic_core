# Security Policy

This repository holds a spec, and the lenses, skills, and agents that
apply it, with small Python scripts that run in CI. The scripts import
the standard library alone, and none of them listens on the network or
calls a model. The gate fetches tools at pinned versions and ships none
of them: `pytest` through `uv` for the tests, `ruff` and `mypy` through
`uvx`, and `markdownlint-cli2` through `npx`. The gate never fetches
`@anthropic-ai/claude-code`. `make plugin` runs it only when `claude`
is already installed, and the CI workflow installs it at a pinned
version so the manifests are validated there.

## Reporting a vulnerability

If you believe you have found a security issue in this repository (for
example a script that could be made to execute untrusted input, or a
skill that instructs an agent to take an unsafe action), do not open an
issue or a pull request. Report it to the maintainer,
[@baristaze](https://github.com/baristaze), in a draft security
advisory of this repository: the Security tab, then Advisories.

You can expect an acknowledgement within a week and a fix or a written
assessment within thirty days.

## Scope

In scope:

- The scripts under `scripts/` and the `Makefile`.
- The workflows under `.github/workflows/`.
- Skill and agent instructions under `skills/` and `agents/` that could
  lead an agent to run a destructive command, exfiltrate data, or write
  outside the repository it was invoked in.

Out of scope:

- The design of the engine in `agentic_core_spec.md` itself. A
  disagreement with a rule is an issue, not a vulnerability.
