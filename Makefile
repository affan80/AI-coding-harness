# One-command pipeline for the harness: `make` (or `make all`) runs venv
# setup, repository hygiene, lint, the test suite, the judge demo scenarios,
# and a live TUI session on the feature fixture.

PY      := .venv/bin/python
RUFF    := .venv/bin/ruff
FIXTURE := demos/fixtures/feature_repo

.DEFAULT_GOAL := all
.PHONY: help all setup hygiene lint test demo tui clean

help:  ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

all: setup hygiene lint test demo tui  ## run everything in one command

# Dev tools are installed directly instead of `pip install -e .`: the repo
# has no build-system and everything runs via `-m` from the repository root.
setup:  ## create .venv and install dev dependencies (idempotent)
	@if [ ! -x "$(PY)" ]; then echo "[make] creating .venv"; uv venv --python 3.12 .venv; fi
	@if ! $(PY) -c 'import pytest' >/dev/null 2>&1 || [ ! -x "$(RUFF)" ]; then \
		echo "[make] installing dev dependencies"; \
		uv pip install --python "$(PY)" 'pytest>=8.0' 'ruff>=0.4'; \
	fi
	@echo "[make] venv ready: $(CURDIR)/.venv"

hygiene:  ## repository hygiene checks via scripts/ci.sh
	@echo "[make] hygiene (scripts/ci.sh)"
	PATH="$(CURDIR)/.venv/bin:$$PATH" ./scripts/ci.sh

lint:  ## ruff check
	@echo "[make] lint"
	$(RUFF) check .

test:  ## pytest suite
	@echo "[make] tests"
	$(PY) -m pytest

demo:  ## demo scenarios A/B/C with evidence reports under runs/demo
	@echo "[make] demo scenarios"
	$(PY) -m demos.run_demo --scenario all --out runs/demo

tui:  ## live TUI session on the feature fixture (M1 stops with PARTIAL; exit 1 is expected)
	@echo "[make] TUI session"
	@$(PY) -m tui.cli $(FIXTURE) "Add a slugify helper with tests without changing existing behavior" --non-interactive; \
	code=$$?; \
	if [ $$code -eq 0 ] || [ $$code -eq 1 ]; then \
		echo "[make] TUI session finished (exit $$code: PARTIAL is the expected M1 outcome)"; \
	else \
		exit $$code; \
	fi

clean:  ## remove run artifacts and caches
	rm -rf runs .pytest_cache .ruff_cache
	find . -name '__pycache__' -not -path './.venv/*' -exec rm -rf {} +
