[//]: # (SPDX-License-Identifier: MIT)
[//]: # (Copyright (c) 2026 Rug Munch Media LLC)
# AGENTS.md — rugmunchbot

> AI agent contract. Read this before touching anything in this repo.

## Status
canonical · owner=crmuncher · last_updated=2026-07-06

## What This Repo Is
rugmunchbot — ACTIVE product: Telegram bot for the Rug Munch Intelligence platform.

## Type
`active-product` · owner=RugMunchMedia · featured OSS

## Where It Lives
- Source: `https://git.rugmunch.io/RugMunchMedia/rugmunchbot`
- Deployed: none (no live deployment)

## Built With
- Standards: [git.rugmunch.io/RugMunchMedia/standards](https://git.rugmunch.io/RugMunchMedia/standards)
- Toolchain: [TOOLCHAIN.md](https://git.rugmunch.io/RugMunchMedia/standards/raw/branch/main/TOOLCHAIN.md)
- Conventions: [CONVENTIONS.md](https://git.rugmunch.io/RugMunchMedia/standards/raw/branch/main/CONVENTIONS.md)

## 🚨 CRITICAL RULES

1. **Active development.** Feature work welcome — follow conventional commits and test coverage rules.
2. **No nested repos.** Don't commit other complete project trees inside this one.
3. **No secrets.** Never commit `.secrets/`, `.env`, `*.pem`, `*.key`, `*.crt`. Use gopass.
4. **No data blobs.** Don't commit `*.zip`, `*.parquet`, model weights, sqlite files.
5. **Coordinate merges with rmi-backend.** When the merge happens, this repo will be archived on Forgejo.
6. **Check docs/ before non-trivial changes.** Coordinate via open issues.

## Domain Boundaries (one domain -> one repo -> one owner)
Canonical contract: [standards/ARCHITECTURE.md](../standards/ARCHITECTURE.md) +
`standards/repo-domains.json`. Enforced by `.importlinter` (pre-push) and
`scripts/repo-boundary-check.sh` (pre-commit).

- **OWNS:** the thin Telegram client — send/receive, command/handler routing,
  formatters, keyboards, channels. API-consumer-only.
- **DOES NOT own:** scanner engine / detectors (rugmunch-scanner), RAG / retrieval
  (rugbrain), the crypto-intelligence API + DataBus (rmi-backend), frontend
  (rmi-frontend), the crawling framework (pryscraper), wallet management
  (walletpress).
- **Boundary:** the bot reaches rmi-backend through its public HTTP API only. It
  never imports `app`, `rugbrain`, `pry`, or `walletpress` internals. [REVIEW]
  the bot still reaches `rugmunch_scanner` directly via its `scanner` extra in
  formatters/handlers/commands — ARCHITECTURE.md says "backend HTTP only"; this
  coupling should be isolated behind `tools/scanner`.

## Commands

There is no live build/test/dev cycle for this repo. It exists only to preserve the code history for reference during the merge into `rmi-backend`.

## Status

Status: active product — see README.md.

## Owners
- Primary: crmuncher
- Backup: TBD

## Related Repos
- `rmi-backend/` — canonical destination for merged code
- See [standards/PRODUCTS.md](https://git.rugmunch.io/RugMunchMedia/standards/raw/branch/main/PRODUCTS.md).