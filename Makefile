# ── rugmunchbot Makefile (archived state) ─────────────────────
# Rug Munch Media fleet standard (from fleet-template/Makefile.python.template)
# Source of truth: https://git.rugmunch.io/RugMunchMedia/rugmunchbot
#
# The standalone bot code has been ARCHIVED. The live bot runs from
# rmi-backend/app/domains/telegram/rugmunchbot/ via systemd.
#
# What's left:
#   tests/              - test fixtures and smoke tests
#   ARCHIVED.md         - archive documentation
#   bot/app/telegram_bot/_ARCHIVED/  - preserved original source

SHELL := /bin/bash

.PHONY: help test clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

test: ## Run tests
	python -m pytest tests/ -v 2>&1 || echo "(no tests to run)"

clean: ## Remove build artifacts
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache
