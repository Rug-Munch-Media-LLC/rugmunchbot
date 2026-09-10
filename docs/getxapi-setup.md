# GetXAPI Setup & Budget Guide

This document explains how the RugMunchBot GetXAPI (X/Twitter API) integration
works, how the $5 budget is enforced so we never overspend, which endpoints we
use and how often, how to add more accounts to monitor, and how to check the
budget remaining.

---

## 1. How the GetXAPI integration works

GetXAPI is a metered X/Twitter API (REST, `https://api.getxapi.com/v1`) that
lets the bot pull real tweet data — searches, user timelines, trends, and
mentions — for the crypto-security intel lanes. Every call costs ~$0.001.

The integration is split into two modules under `src/rugmunchbot/tools/`:

| Module | Responsibility |
|--------|----------------|
| `getxapi_config.py` | Central config: API key, base URL, budget numbers, polling intervals, monitored accounts. All env-overridable. |
| `getxapi_budget.py` | Redis-backed budget tracker: daily call count + total spend, `can_afford_call()` gate, warning/critical logging, budget report. |

**The flow:** before making any GetXAPI request, the caller checks
`can_afford_call()`. If it returns `False` (daily cap or total budget hit), the
call is **not made**. After a successful call, `record_call(endpoint)` is
invoked to increment the daily counter and total spend in Redis.

**Fail-open:** if Redis is down, `can_afford_call()` returns `True` and
`record_call()` logs a warning — a dead Redis never blocks the X lanes. The
cost is a brief window of un-metered calls.

---

## 2. Budget strategy ($5 = 4,500 calls, 150/day max, lasts 30+ days)

GetXAPI charges ~$0.001 per call, so:

- **$5 budget ≈ 4,500 calls** (`GETXAPI_TOTAL_BUDGET` = `5.0`).
- **150 calls/day max** (`GETXAPI_DAILY_CALL_LIMIT` = `150`).
- **Lifespan:** 4,500 / 150 = **30 days** minimum.

The budget tracker enforces **two hard caps**:

1. **Daily call cap (150/day)** — `can_afford_call()` returns `False` once
   today's count reaches 150. This is the per-day ceiling.
2. **Total spend cap ($5.00)** — `can_afford_call()` returns `False` once
   cumulative spend reaches $5.00. This is the hard lifetime ceiling.

Both are stored in Redis and survive restarts:

| Redis key | Purpose | TTL |
|-----------|---------|-----|
| `getxapi:calls:{YYYYMMDD}` | Daily call counter (INCR) | 48h |
| `getxapi:spend` | Cumulative spend in dollars (INCRBYFLOAT) | none (lifetime) |

**Early-warning thresholds** (logged once per crossing, not per call):

| Threshold | Level | Meaning |
|-----------|-------|---------|
| daily calls > 100 | WARNING | Approaching the daily limit |
| daily calls > 140 | CRITICAL | Daily limit nearly exhausted |
| total spend > $4.50 | CRITICAL | 90% of the $5 budget spent |

---

## 3. What endpoints we use and how often

The polling intervals are tuned so the sum of all scheduled lanes stays well
under the 150/day cap. All are env-overridable.

| Lane | Endpoint family | Interval | ~Calls/day |
|------|-----------------|----------|-----------|
| Search | `search` | 30 min (`GETXAPI_SEARCH_INTERVAL_MIN`) | ~48 |
| User tweets | `user-tweets` | 6 h (`GETXAPI_USER_TWEETS_INTERVAL_HOURS`) | ~4/account |
| Trends | `trends` | 60 min (`GETXAPI_TRENDS_INTERVAL_MIN`) | ~24 |
| Mentions | `mentions` | 60 min (`GETXAPI_MENTIONS_INTERVAL_MIN`) | ~24 |

**Budget arithmetic:** with the default intervals, the search + trends +
mentions lanes alone account for ~96 calls/day. The user-tweets lane adds
~4 calls/day per monitored account. With 15 accounts that would be ~60/day —
so in practice the user-tweets lane is round-robined (only a subset of
accounts per tick) to keep the total comfortably under 150/day. If you add
accounts or shorten intervals, re-check the arithmetic against the daily cap.

---

## 4. How to add more accounts to monitor

The monitored security accounts live in `GETXAPI_SECURITY_ACCOUNTS` (default
15 accounts). To add more:

1. **Env var (recommended):** set `GETXAPI_SECURITY_ACCOUNTS` to a
   comma-separated list of handles (no `@` prefix needed):
   ```bash
   GETXAPI_SECURITY_ACCOUNTS=zachxbt,peckshield,blockaid,certikalert,slowmist,beosin,chainalysis,ledger,metamask,rugmunchbot,scamsniffer,rektnews,officiallychad,spotonchain,lookonchain,newaccount1,newaccount2
   ```
2. **Code default:** edit `GETXAPI_SECURITY_ACCOUNTS` in
   `src/rugmunchbot/tools/getxapi_config.py`.

> **Budget note:** each monitored account adds ~4 user-tweets calls/day. Keep
> the total (search + trends + mentions + accounts × 4) under 150/day, or the
> daily cap will start blocking calls.

---

## 5. How to check budget remaining

### In code

```python
from rugmunchbot.tools.getxapi_budget import get_daily_usage, get_budget_report

usage = get_daily_usage()
# {"calls": 42, "spend": 0.042, "budget_remaining": 4.958, "calls_remaining": 108}

print(get_budget_report())
# "GetXAPI budget: 42/150 calls today, spent $0.0420 of $5.00 (remaining $4.9580), 108 calls remaining"
```

`get_daily_usage()` returns:
- `calls` — calls made today
- `spend` — total dollars spent (lifetime)
- `budget_remaining` — `$5.00 − spend`
- `calls_remaining` — the **smaller** of (daily cap − calls) and
  (budget_remaining / cost_per_call) — the true number of calls still
  affordable today.

### Directly in Redis

```bash
# Today's call count
redis-cli GET "getxapi:calls:$(date -u +%Y%m%d)"
# Total spend (dollars)
redis-cli GET getxapi:spend
```

### In logs

The budget tracker logs:
- `getxapi_budget:approaching_daily_limit` (WARNING, calls > 100)
- `getxapi_budget:daily_limit_nearly_exhausted` (CRITICAL, calls > 140)
- `getxapi_budget:budget_nearly_exhausted` (CRITICAL, spend > $4.50)
- `getxapi_budget:blocked_daily_limit` / `blocked_budget` (WARNING, a call was refused)

---

## 6. API key setup

The key never enters git. Resolution order (mirrors the ApiPass lane):

1. Env var `GETXAPI_KEY`
2. Key file `$RMI_SECRETS_DIR/getxapi_api_key` (default `/srv/secrets/getxapi_api_key`)
3. gopass `[INTERNAL_SECRET_PATH]`

```bash
# Store the key in gopass
gopass insert [INTERNAL_SECRET_PATH]
# Or drop it in the secret-store file
echo -n "your-key" > /srv/secrets/getxapi_api_key && chmod 600 /srv/secrets/getxapi_api_key
```

`api_key_configured()` (from `getxapi_config.py`) reports whether a key is
available. The budget tracker works regardless of key presence — it only
meters calls that actually happen.
