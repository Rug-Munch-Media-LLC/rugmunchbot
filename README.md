# rugmunchbot

The Telegram bot: paste a token or wallet address, get a risk verdict, a chart, or
a portfolio view in the chat, plus watchlist alerts when something changes.

**Status:** production — this is the bot serving `@rugmunchbot`.
**Licence:** Rug Munch Intelligence Source Available 1.0 — see [LICENSE](LICENSE).

Canonical: https://git.rugmunch.io/RugMunchMedia/rugmunchbot

## Quickstart

Python 3.11+, Redis, and a bot token from BotFather.

```bash
git clone https://git.rugmunch.io/RugMunchMedia/rugmunchbot.git
cd rugmunchbot
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # TELEGRAM_BOT_TOKEN, REDIS_URL, RMI_BACKEND_URL
python -m rugmunchbot.bot
```

The bot exposes a health endpoint on `:9191` (`/healthz`), which is what the
container healthcheck probes.

Compose runs the bot and its Redis together and refuses to start without the
token:

```bash
export TELEGRAM_BOT_TOKEN='...'
docker compose up -d
```

## What it does

- **Scan in chat.** A pasted contract address returns the verdict and the flags
  that produced it, with the evidence, not just a number.
- **Wallet and portfolio.** Balances and P&L across the chains the backend
  supports.
- **Charts.** RugCharts output delivered as an image in the conversation.
- **Watchlists and alerts.** `broadcast_worker.py` fans out price and scam alerts
  to subscribers; Redis holds the queue so a restart does not drop pending sends.
- **Commands are data.** `command_lookup.py` and `configs/` define the command
  surface, so adding one is a config change plus a handler.

## How it is put together

```
src/rugmunchbot/
  bot.py               entry point (`python -m rugmunchbot.bot`)
  command_lookup.py    command registry
  data_resolver.py     backend calls, caching
  broadcast_worker.py  alert fan-out
  webhook_manager.py   webhook vs polling switch
  model_router.py      optional LLM routing for free-text questions
  ta_indicators.py     indicator maths for chart views
```

## Configuration

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | required; the process exits without it |
| `REDIS_URL` | queue, rate limits, subscriber state |
| `RMI_BACKEND_URL`, `RMI_API_KEY` | intelligence API and its credential |
| `ADVANCED_SCAN_URL` | separate endpoint used by the deep-scan command |
| `BOT_OWNER_IDS` | Telegram user ids allowed to run admin commands |
| `BOT_CHANNELS` | channels the broadcast worker may post to |
| `BOT_FREE_SCANS_PER_WEEK`, `BOT_FREE_AI_MSGS_PER_WEEK` | free-tier quotas |
| `BOT_DEX_REF_LINKS`, `RMI_REF_GMGN` | referral codes attached to links |

Command reference lives in `COMMANDS.md`.

## Known limits

- Source-available, not open source. Running your own copy is fine for internal
  use; redistributing it is not.
- Needs a working `rmi-backend`. The bot is a client and holds no analysis code.
- Free-text questions depend on an LLM lane being configured on the backend; with
  no key, those commands answer with a canned message.
- Telegram rate limits are enforced by the library, not by us — a large broadcast
  is throttled, not queued faster.

## Licence

Rug Munch Intelligence Source Available License 1.0. See [LICENSE](LICENSE).
