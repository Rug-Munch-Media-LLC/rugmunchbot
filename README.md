[//]: # (SPDX-License-Identifier: LicenseRef-Source-Available-1.0)
[//]: # (Copyright (c) 2026 Rug Munch Media LLC)
# RugMunchBot

> Self-hostable Telegram bot for crypto intelligence: token scanning, wallet forensics, market data and AI-powered risk analysis.

**License:** [Source Available 1.0](LICENSE) · **Canonical:** https://git.rugmunch.io/RugMunchMedia/rugmunchbot
**Mirrors:** [GitHub](https://github.com/Rug-Munch-Media-LLC) · [GitLab](https://gitlab.com/rug-munch-media-group) · [Codeberg](https://codeberg.org/RugMunchMedia)


> **Status: COMPLETE — the bot lives in its own repo (single brain).**
> `src/rugmunchbot` is the canonical bot code (runs standalone, systemd uses
> this repo's venv). Vendored platform modules are frozen copies from
> rmi-backend — refresh them with `bash scripts/refresh-vendors.sh` after
> backend changes to those modules (see
> [`docs/RUGMUNCHBOT-EXTRACTION.md`](docs/RUGMUNCHBOT-EXTRACTION.md)).

## Features

- 🔍 **Token scanning** — honeypot, rug-pull, bundle, wash-trade, and
  fresh-wallet detection across major EVM chains and Solana (public APIs:
  DexScreener, GoPlus, Honeypot.is)
- 👛 **Wallet forensics** — P&L, counterparty analysis, dev wallet history
- 📊 **Market intelligence** — prices, trending, news, technical analysis
- 🤖 **AI chat** — free-text questions grounded in live data, with a local
  LLM fallback (no cloud dependency required)
- 🚨 **Alerts & watchlists** — real-time price and scam alerts
- 🛡️ **Anti-abuse** — flood control, secret-extraction heuristics,
  rate-limited owner alerting

## Quickstart (self-host)

### 1. Install

```bash
git clone <this-repo-url> && cd rugmunchbot
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure

```bash
cp .env.example .env
# Minimum for a running bot:
export TELEGRAM_BOT_TOKEN="123456:ABC-DEF..."   # from @BotFather
```

All configuration is environment-driven. Nothing is hardcoded — tokens,
channel IDs, owner IDs, referral codes, and quotas come from env vars (see
[`.env.example`](.env.example) and `src/rugmunchbot/config.py`).

### 3. Run

```bash
python -m rugmunchbot
```

The bot creates its SQLite database (`data/bot_users.db` by default) on
first run.

### 4. Optional backends

| Backend | Env | Default |
|---|---|---|
| Local LLM (chat fallback) | `BOT_LLM_API_BASE`, `BOT_LLM_MODEL` | off (chat disabled) |
| Birdeye price API | `BIRDEYE_API_KEY` | off (CoinGecko/DexScreener used) |
| SearXNG meta-search | `SEARXNG_URL` | `http://127.0.0.1:8889` |
| Operator scanner backend | `RMI_BACKEND_URL`, `RMI_API_KEY` | off (public APIs used) |
| Redis flood-control cache | `REDIS_URL`/`REDIS_HOST`/`REDIS_PORT` | off (SQLite fallback) |

## Architecture

```
src/rugmunchbot/
├── bot.py            Application build + entry (main)
├── config.py         Env-driven configuration (no secrets)
├── db.py             SQLite persistence (users, quota, ref codes)
├── scanner.py        Scan orchestration (ScanProvider protocol)
├── handlers/         Command & message handlers (28 modules)
├── commands/         Slash-command implementations
├── tools/            Live-data tool registry (21 tools)
├── formatters/       Report/text/keyboard builders
├── services/         Chart/image generation (Pillow)
├── support/          Self-contained logging shim
└── plugins/          Provider protocols: Billing, Scan, Data, LLM
```

Provider seams (`plugins/`) are the extension points: bring your own
scanner backend, billing, LLM router, or data provider without forking the
bot.

## Security & privacy

- **No secrets in the repo.** Everything comes from the environment.
- **No user data is collected by the project.** The SQLite DB is local to
  your deployment.
- Telegram Stars / premium billing is **not** part of this OSS build — it is
  a `BillingProvider` you can implement yourself.

## Tests

```bash
make test
```

> Note: the stale `tests/` (exercising the removed deprecated `bot/` tree) were
> removed in Phase 0. Core behavior is verified by the standalone import/smoke
> gates in CI and the refresh-vendors invariant checks.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
Conventional commits required; lint via `make lint`; types via
`make typecheck`.

## License

Source Available 1.0 — see [LICENSE](LICENSE).

## FAQ

See [docs/FAQ.md](docs/FAQ.md) for frequently asked questions.
