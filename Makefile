# ── rugmunchbot Makefile ──────────────────────────────────────
# Rug Munch Media fleet standard (from fleet-template/Makefile.python.template)
# Source of truth: https://git.rugmunch.io/RugMunchMedia/rugmunchbot
#
# CryptoRugMunch Telegram bot (v6) - the Bloomberg Terminal of Shitcoins.
# Runs against the deployed rmi-backend (Talos) for data + referrals.
#
# Layout:
#   bot/app/telegram_bot/bot.py                 - main bot loop (polling)
#   bot/app/telegram_bot/commands/              - /scan, /portfolio, /watch, ...
#   bot/app/telegram_bot/config.py              - env + tier config
#   bot/app/telegram_bot/db.py                  - sqlite store (tiers, users, watches)
#   bot/app/telegram_bot/referrals.py           - referral link tracking
#   bot/app/telegram_bot/requirements.txt       - python-telegram-bot[job-queue]==21.10

SHELL := /bin/bash

.PHONY: help install dev build lint format check test typecheck security \
        precommit commit ci clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install deps from telegram_bot/requirements.txt
	pip install -r bot/app/telegram_bot/requirements.txt

dev: ## Run bot locally (long-polling Telegram)
	@test -n "$$TELEGRAM_BOT_TOKEN" || (echo "TELEGRAM_BOT_TOKEN not set" && exit 1)
	cd bot && python3 -m app.telegram_bot.bot

build: ## Build bot Docker image (matches Dockerfile in telegram_bot/)
	docker build -t rugmunchbot bot/app/telegram_bot/

lint: ## Lint bot/app/ with ruff
	ruff check bot/app/

format: ## Format bot/app/ with ruff (writes)
	ruff format bot/app/
	ruff check --fix bot/app/

check: ## Full check: lint + format + typecheck
	ruff check bot/app/
	ruff format --check bot/app/
	mypy bot/app/ --ignore-missing-imports

typecheck: ## MyPy type check
	mypy bot/app/ --ignore-missing-imports

test: ## Run tests (placeholder - no tests yet)
	@echo "no tests yet - see TESTING.md if added"
	exit 0

security: ## Security audit (bandit + ruff S-rules)
	bandit -r bot/app/ -q
	ruff check --select S bot/app/

precommit: ## Install pre-commit hooks
	pre-commit install

commit: ## Interactive commit with commitizen
	cz commit || git commit

ci: lint typecheck security ## Full CI: lint + typecheck + security

clean: ## Remove build artifacts
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache
