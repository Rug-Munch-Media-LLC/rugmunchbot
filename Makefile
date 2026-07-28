# ── rugmunchbot Makefile ─────────────────────────────────────────
# Rug Munch Media fleet standard (from fleet-template/Makefile.python.template)
# Source of truth: https://git.rugmunch.io/RugMunchMedia/rugmunchbot
#
# The standalone bot code at bot/app/telegram_bot/ is ACTIVE and TOP TIER.
# This is the @rugmunchbot Telegram bot — not dead code.
#
# Targets:
#   test        Run tests
#   lint        Run ruff linter + formatter check
#   typecheck   Run mypy type checking
#   clean       Remove build artifacts

SHELL := /bin/bash

.PHONY: help test lint typecheck clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

test: ## Run tests
	python -m pytest tests/ -v 2>&1

lint: ## Run ruff linter + formatter check
	ruff check .
	ruff format --check .

typecheck: ## Run mypy type checking
	mypy bot/app/telegram_bot/ tests/

clean: ## Remove build artifacts
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache
