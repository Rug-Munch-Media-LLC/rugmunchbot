# ── rugmunchbot Makefile ─────────────────────────────────────────────
# Rug Munch Media fleet standard (from fleet-template/Makefile.python.template)

SHELL := /bin/bash

.PHONY: help test voice-eval lint typecheck policy clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

test: voice-eval ## Run tests
	python -m pytest tests/ -v 2>&1

VOICE_MIN ?= 0.75

voice-eval: ## Voice gate — deterministic voice scorer (fails below VOICE_MIN)
	python3 eval/voice/score.py --min $(VOICE_MIN)

lint: ## Run ruff linter + formatter check
	ruff check src/ tests/
	ruff format --check src/ tests/

typecheck: ## Run mypy type checking
	mypy src/rugmunchbot/ tests/

policy: ## Run good-code policy checks (docs/GOOD-CODE-POLICY.md)
	bash scripts/check-policy.sh

clean: ## Remove build artifacts
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache
