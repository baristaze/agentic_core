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

.PHONY: help check lint ruff mypy lenses links toc version skills agents test plugin gen-skills gen-skills-check gen-toc demo demo-check clean

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

demo:              ## the engine in process, its run scripted end to end, on one screen of 120 by 36
	@cd demo && $(DEMO)

demo-check:        ## the demo headless, offline, with a provider's key set: each act and the ledger checked against the record
	cd demo && env $(DEMO_KEYS) $(DEMO) --check

clean:             ## remove tool caches
	rm -rf .markdownlint-cli2-cache node_modules .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -not -path './node_modules/*' -prune -exec rm -rf {} +
