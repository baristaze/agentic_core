# agentic_core: checks and generators
SHELL := /bin/bash
# The scripts run through uv, so they run on the Python uv selects: the
# one UV_PYTHON names (CI sets it per leg), else the one on PATH.
PYTHON := uv run --no-project python
# Every tool runs at the version .github/pins/ names, one file per
# ecosystem, so the Makefile and CI run the same release of each.
PINS := .github/pins
pin = $(shell sed -n 's|^$(1)==||p' $(PINS)/requirements.txt)
npm_pin = $(shell sed -n 's|^ *"$(1)": *"\([^"]*\)".*|\1|p' $(PINS)/package.json)
# The tests need pytest, which a system python3 may not carry; uv brings
# it at the pinned version, locally and in CI alike.
PYTEST := uv run --no-project --with pytest==$(call pin,pytest) python -m pytest
NPX := npx --yes
MARKDOWNLINT := $(NPX) markdownlint-cli2@$(call npm_pin,markdownlint-cli2)
# ruff and mypy run at pinned versions through uvx; pyproject.toml holds their configuration
RUFF := uvx ruff@$(call pin,ruff)
MYPY := uvx --with pytest==$(call pin,pytest) mypy@$(call pin,mypy)
# The engine's static checker lives in the scaffold, so every copy
# carries it; its tests run here too, from the same source.
CHECKER := scaffold/acme_root/checkers
# The demo runs the scaffold's engine as it sits, on the Python and the
# packages the scaffold's lock names.
DEMO := uv run --project ../scaffold/acme_root --package acme-om --no-dev --locked python -m dreamcatcher
# A provider's key, as a viewer's shell may hold one, under each name the
# engine's settings and the providers read: the check holds that the demo
# builds no provider that would send it.
DEMO_KEYS := ANTHROPIC_API_KEY=never-sent OPENAI_API_KEY=never-sent ACME_LLM_PROVIDERS=live \
	ACME_ANTHROPIC_API_KEY=never-sent ACME_OPENAI_API_KEY=never-sent
# The performance run builds the engine as the session runner does, so it
# runs on the runner's package and the scaffold's lock.
PERFORMANCE_RUN := uv run --project ../scaffold/acme_root --package acme-agent-session-runner --no-dev --locked python
# Its stack is a compose project of its own; `make performance` starts it,
# and stops what it started. Each port is a knob of performance/compose.yml.
PERFORMANCE_PROJECT ?= agentic-performance
PERFORMANCE_COMPOSE := docker compose -p $(PERFORMANCE_PROJECT) -f $(CURDIR)/performance/compose.yml
PERFORMANCE_RESULTS ?= results/$(shell date -u +%Y-%m-%dT%H%MZ).json
PERFORMANCE_ARGS ?=

.PHONY: help check lint ruff mypy lenses links toc version skills agents test plugin gen-skills gen-skills-check gen-toc demo demo-check demo-gif performance performance-check clean

help:              ## show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

check: lint ruff mypy lenses links toc version gen-skills-check skills agents test plugin ## run every check (what CI runs)

lint:              ## markdownlint over every Markdown file
	$(MARKDOWNLINT) "**/*.md" "#node_modules"

ruff:              ## lint and format check of scripts/, tests/, demo/, and the scaffold, which its own ruff.toml configures
	$(RUFF) check
	$(RUFF) format --check

mypy:              ## type check of scripts/, tests/, and demo/
	$(MYPY)

lenses:            ## every lens follows the format and cites a real section of the spec
	$(PYTHON) scripts/check_lenses.py

links:             ## every relative link and anchor resolves
	$(PYTHON) scripts/check_links.py

toc:               ## the Contents of agentic_core_spec.md matches its headings
	$(PYTHON) scripts/gen_toc.py --check

version:           ## every copy of the release version agrees with .claude-plugin/plugin.json
	$(PYTHON) scripts/check_version.py

skills:            ## every skill, the scaffold's included, has valid frontmatter and names files that exist; one review skill per lens group
	$(PYTHON) scripts/check_skills.py

agents:            ## every subagent under agents/ has valid frontmatter and caps its turns; the reviewer mirrors the review template
	$(PYTHON) scripts/check_agents.py

test:              ## the scripts and the scaffold's agentic-check pass their own tests (pytest through uv, pinned)
	$(PYTEST) tests $(CHECKER)/tests -q

plugin:            ## validate the plugin, marketplace, skills, and agents with Claude Code (skipped when claude is not installed)
	@if command -v claude >/dev/null 2>&1; then \
	  claude plugin validate . --strict \
	  && claude plugin validate .claude-plugin/plugin.json --strict \
	  && for dir in skills agents; do if [ -d "$$dir" ]; then claude plugin validate "$$dir" --strict || exit 1; fi; done; \
	else echo "plugin: claude not installed, skipped"; fi

gen-skills:        ## regenerate the review skills from the template and the lens files
	$(PYTHON) scripts/gen_skills.py

gen-skills-check:  ## fail when a generated skill is out of date
	$(PYTHON) scripts/gen_skills.py --check

gen-toc:           ## regenerate the Contents of agentic_core_spec.md
	$(PYTHON) scripts/gen_toc.py

demo:              ## the engine in process, its run written to the scene's tape, and the scene opened in a browser
	@cd demo && $(DEMO)

demo-check:        ## the demo headless, offline, with a provider's key set: each act, the ledger, and the tape checked against the record
	cd demo && env $(DEMO_KEYS) $(DEMO) --check

demo-gif:          ## the scene recorded into demo/dreamcatcher.gif in headless Chromium, frame by frame (needs ffmpeg; CI does not run it)
	cd demo && $(DEMO) --tape && uv run --no-project --with playwright==$(call pin,playwright) python record.py

performance:       ## the replay on its own stack (Postgres per role, Valkey, MinIO, a KMS emulator), alone and in a sweep; writes performance/$(PERFORMANCE_RESULTS)
	@if [ -n "$$($(PERFORMANCE_COMPOSE) ps -q --status running 2>/dev/null)" ]; then \
	  echo "performance: the project $(PERFORMANCE_PROJECT) is running; its owner stops it first"; exit 1; fi
	@trap '$(PERFORMANCE_COMPOSE) stop' EXIT; \
	$(PERFORMANCE_COMPOSE) up -d --wait \
	&& cd performance && set -a && . ./stack.env && set +a \
	&& $(PERFORMANCE_RUN) -m acme.om.storage.migrate ensure-logins --local \
	&& $(PERFORMANCE_RUN) -m acme.om.storage.migrate upgrade --all --local \
	&& env $(DEMO_KEYS) $(PERFORMANCE_RUN) -m replay --full $(PERFORMANCE_RESULTS) $(PERFORMANCE_ARGS)

performance-check: ## a short replay on memory storage and the key twin, with a provider's key set: each step read back equal to what was said, and sealed at rest
	cd performance && env $(DEMO_KEYS) $(PERFORMANCE_RUN) -m replay --check

clean:             ## remove tool caches
	rm -rf .markdownlint-cli2-cache node_modules .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -not -path './node_modules/*' -prune -exec rm -rf {} +
