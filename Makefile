# agentic_core: checks
SHELL := /bin/bash
# The scripts run through uv, on the Python uv selects.
PYTHON := uv run --no-project python
PYTEST := uv run --no-project --with pytest==9.1.1 python -m pytest

.PHONY: help check lenses test

help:              ## show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

check: lenses test ## run every check

lenses:            ## every lens follows the format and cites a real section of the spec
	$(PYTHON) scripts/check_lenses.py

test:              ## the scripts pass their own tests (pytest through uv, pinned)
	$(PYTEST) tests -q
