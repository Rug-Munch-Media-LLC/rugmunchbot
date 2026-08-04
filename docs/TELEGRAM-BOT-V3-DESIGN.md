# RugMunchBot V3 — Design Document

> **Status:** Proposed design · **Owner:** crmuncher · **Created:** 2026-08-04
> **Scope:** Scanner integration (RugMunchScan), SENTINEL deep scans, professional command menu, automated alert channels, rated news channels, Qwen3.5-9B AI chat.
> **Repo context:** This design targets the canonical live bot at `rmi-backend/app/domains/telegram/rugmunchbot/` and the standalone reference at `bot/app/telegram_bot/`. See [Codebase State](#codebase-state) for the dual-tree reconciliation note.

---

## 1. Executive Summary

RugMunchBot V3 turns @rugmunchbot from a single-purpose scanner into a full
**intelligence terminal**: one professional command surface that routes through
four backend capabilities:

| Capability | Backend | Live today |
|---|---|---|
| Token security scan (RugMunchScan) | `POST /api/v1/scanner/scan` (v1) → v2 contract | ⚠️ partial |
| SENTINEL deep forensic scan | `POST /api/v2/scanner/token/scan?depth=deep` (proposed) | ❌ planned |
| AI chat (Qwen3.5-9B via llama-server) | `POST http://localhost:8093/v1/chat/completions` | ❌ not wired |
| RAG intelligence bridge | `POST /api/v1/rag/v2/search` | ✅ wired |
| Rated news aggregator | `GET /api/v1/internal/news-feed` | ⚠️ partial |
| Market overview / fear & greed | `GET /api/v1/market/fear-greed`, `/market/overview` | ⚠️ partial |
| Automated alert push | `POST /api/v1/alerts/webhook` (backend→bot) | ❌ 501 stub |

The design keeps the existing monolith-compatible handler style while adding
three new handler modules and two background pipelines (alert push + hourly news).

---

## 2. Codebase State

Two bot codebases exist and are not synced ([REVIEW] — TODO.md "Decide archive story"):

1. **Canonical live bot** — `rmi-backend/app/domains/telegram/rugmunchbot/`
   Modular: `handlers/` (scanning, alerts, market, message, callback…), `scanner.py`,
   `states.py`, `keyboards.py`, `formatters.py`, plus `rmi-backend/app/domains/telegram/alert_hooks.py`.
2. **Standalone reference** — this repo `bot/app/telegram_bot/bot.py`
   Single-file monolith (~3k lines) with scan engine, wallet forensics, DEX ref links,
   Stars payments, admin commands. AGENTS.md marks this repo "orphan / no new features",
   but ARCHIVED.md (2026-07-28) and TODO.md (2026-07-31) reversed that: the standalone
   bot IS the product and top-tier.

**V3 placement decision:** implement V3 in the **canonical live bot**
(`rmi-backend/app/domains/telegram/rugmunchbot/`) because it is deployed and modular.
This design document lives in the standalone repo as the authoritative blueprint and
mirrors each module name so porting is mechanical. ([REVIEW] — confirm with owner which
tree receives the code; this doc covers both with identical module names.)

---

## 3. Part 1 — Command Menu Architecture

### 3.1 Command reference (V3 surface)

```
/start     — Welcome + main menu (inline keyboard)
/scan [CA] — Token security scan via RugMunchScan (risk score, warnings, liquidity, deployer)
/deepscan [CA] — SENTINEL deep forensic scan (premium, 250★ or Scout+)
/analyze [CA] — AI analysis of a token via Qwen3.5-9B + RAG context
/risk [CA]   — Quick risk score (lightweight, no full report)
/market      — Market overview: fear & greed index, movers, sentiment
/alerts [on|off] [security|whale|price] — Manage alert subscriptions
/news [topic] — Latest rated news; topics: defi, security, general
/chat [text] — Chat with RugMunch AI (Qwen3.5-9B); also catches non-command text
/help        — Full categorized command guide
```

Backward-compatible aliases kept: `/wallet`, `/ta`, `/compare`, `/quick`, `/trending`,
`/watch`, `/watchlist`, `/alerts` (extended), `/account`, `/pricing`, `/topup`,
`/research`, `/scamschool`, `/refer`, `/rugcheck`.

### 3.2 Menu hierarchy (inline keyboard flow)

```
/start
  └─ main_menu
       ├─ 🔍 Scan Token        → menu_scan      (prompt for address)
       ├─ 🧠 Deep Scan         → menu_deepscan   (premium gate)
       ├─ 🤖 AI Analysis       → menu_analyze    (prompt for address/symbol)
       ├─ ⚡ Quick Risk        → menu_risk       (prompt for address)
       ├─ 📊 Market Overview   → menu_market     (fear&greed + movers)
       ├─ 📰 News              → menu_news       (top rated)
       ├─ 👛 Wallet Check      → menu_wallet     (legacy)
       ├─ 🔔 Alerts            → menu_alerts     (subscription toggle)
       ├─ 👤 Account           → menu_account
       ├─ ⭐ Upgrade           → menu_pricing
       └─ 📚 Scam School       → menu_scamschool
```

Scan result inline actions (from `/scan` or callback):

```
[scan report message]
  ├─ 🔬 Deep Scan (SENTINEL)   → deep_{addr}_{chain}
  ├─ 🤖 AI Analysis            → ai_{addr}_{chain}
  ├─ 📊 Chart                  → chart_{addr}_{chain}  (DexScreener/Birdeye url)
  ├─ 👀 Watch                  → watch_{addr}_{chain}
  ├─ 🛡️ Full Report on Web     → web scan button (WEB_APP_URL?token=)
  └─ DEX trade links (existing DEX_REF_LINKS rows)
```

Callback data namespace (all existing prefixes preserved, new ones added):

| Prefix | Meaning |
|---|---|
| `menu_*` | Main menu navigation |
| `scan_*` / `deep_*` / `ai_*` / `chart_*` | Scan-result actions |
| `watch_*` / `unwatch_*` | Watchlist |
| `sub_*` / `topup_*` | Billing |
| `alerts_*` | Alert subscription toggles |
| `news_*` | News topic buttons / pagination |

### 3.3 Bot command registration (BotFather set_my_commands)

Priority-ordered command list for the UI menu (Telegram shows the first 8–10):

```
start, scan, deepscan, analyze, risk, market, alerts, news, chat, help
```

---

## 4. Part 2 — Scanner Integration (RugMunchScan)

### 4.1 Target contract

**`POST /api/v2/scanner/token/scan`** (proposed v2 — currently v1 exists at
`POST /api/v1/scanner/scan` which is async and returns `scan_id` + poll
`GET /api/v1/scanner/scan/{scan_id}`).

Request:
```json
{
  "chain": "solana",
  "address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
  "depth": "quick"            // quick | standard | deep
}
```
Auth header: `X-API-Key: {RMI_AUTH_TOKEN}` (matches existing backend pattern).

Response (v2 desired — synchronous full report):
```json
{
  "scan_id": "scan_abc123",
  "status": "completed",
  "risk_score": 72,
  "risk_tier": "high",
  "factors": ["honeypot", "low_liquidity", "fresh_wallets"],
  "warnings": ["HONEYPOT DETECTED", "LP lock < 50%"],
  "liquidity": { "usd": 125000, "locked_pct": 0.3 },
  "deployer": { "address": "...", "other_tokens": 3, "reputation": "suspicious" },
  "market": { "price_usd": 0.0012, "mcap_usd": 500000, "volume_24h": 20000 },
  "holders": { "total": 1200, "top10_pct": 68.0, "fresh_wallet_pct": 41.0 },
  "contract": { "verified": false, "mintable": true, "owner_renounced": false,
                "buy_tax_pct": 8.0, "sell_tax_pct": 12.0 },
  "ts": "2026-08-04T12:00:00Z"
}
```

### 4.2 Bot-side wiring

New module `handlers/scanning.py` (or extend existing) — `scan_token()` client:

```python
async def scan_token_v2(address: str, chain: str | None = None,
                        depth: str = "quick") -> dict:
    """Call RugMunchScan v2; fall back to v1 async + poll if v2 unavailable."""
    payload = {"chain": chain or detect_chain(address), "address": address, "depth": depth}
    async with httpx.AsyncClient(timeout=30) as client:
        # 1) try v2 synchronous
        try:
            r = await client.post(f"{BACKEND_URL}/api/v2/scanner/token/scan",
                                  json=payload, headers=backend_headers())
            if r.status_code == 200:
                return r.json()
        except (httpx.HTTPError, httpx.TimeoutException):
            pass  # fall through to v1
        # 2) v1 async + poll (max 8 tries, 2s apart)
        r = await client.post(f"{BACKEND_URL}/api/v1/scanner/scan",
                              json=payload, headers=backend_headers())
        scan_id = r.json().get("scan_id")
        for _ in range(8):
            await asyncio.sleep(2)
            poll = await client.get(f"{BACKEND_URL}/api/v1/scanner/scan/{scan_id}",
                                    headers=backend_headers())
            body = poll.json()
            if body.get("status") in ("completed", "failed"):
                return body
    raise ScanUnavailableError("Scanner backend unreachable")
```

- **Existing `scan_token()`** (DataBus-based, in both trees) remains the price/security
  enrichment layer. V3 strategy: **try v2 RugMunchScan first**; if it fails, fall back to
  the existing DataBus path so `/scan` never regresses.
- **Rate limiting / quotas:** `/scan`, `/deepscan`, `/analyze`, `/risk` all consume the
  existing `db.check_rate_limit(user_id, "scan")` quota; `/deepscan` additionally gates on
  premium tier (Scout+ or Stars 250, reuse `SCAN_PACKS["deep_scan"]`).
- **Input validation:** reuse `EVM_RE` / `SOL_RE` / `detect_chain`; reject unknown chains
  with a friendly message (<2s response, no exceptions).

### 4.3 Scan report formatting

`format_scan_report()` extension to include v2 fields:

```
🛡️ RMI Token Scan
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{Name} ({SYMBOL}) ◎ Solana | Age: 2d
{address}

📊 Market Data
────────────────────────
💰 Price: $0.0012
📈 Market Cap: $500.0K
💧 Liquidity: $125.0K
🔒 LP Locked: 30%
📊 24h Volume: $20.0K

🔒 Security  [risk_bar]
🟠🟠🟠🟠🟠🟠🟠⬜⬜⬜ 72% RISKY
  🔴 HONEYPOT DETECTED
  🔴 Low LP lock: 30%
  🔴 High fresh wallet ratio: 41%

🔬 Advanced
────────────────────────
🕸️ Deployer: 3 other tokens (suspicious)
👥 Top10 holders: 68.0%

💱 Trade: [dex buttons]
🌐 rugmunch.io | 📢 Alerts | X
```

Inline buttons: **🔬 Deep Scan · 🤖 AI Analysis · 📊 Chart · 👀 Watch**.

---

## 5. Part 3 — AI Chat Integration (Qwen3.5-9B)

### 5.1 Endpoint

`POST {LLM_CHAT_URL}` — default `http://localhost:8093/v1/chat/completions`
(OpenAI-compatible llama-server, model `qwen3.5-9b`, already in `config.py` uncommitted).

```json
{
  "model": "qwen3.5-9b",
  "messages": [
    {"role": "system", "content": "<system prompt with hardened directives>"},
    {"role": "user", "content": "Is this token safe? 0x6982..."}
  ],
  "temperature": 0.3,
  "max_tokens": 600,
  "stream": false
}
```

### 5.2 Guardrails — reuse `chat_guardrails.py`

The existing system in `rmi-backend/app/security/chat_guardrails.py` MUST be used for
every AI path. It is stdlib-only and standalone-deployable:

- `ChatGuardrails.sanitize_input(message) -> (allowed, message)` — blocks prompt
  injection / hacking / internal probing; escapes delimiter tokens.
- `ChatGuardrails.classify(text) -> ScanResult` — allow | block | disclaimer
  (financial advice → disclaimer, not block).
- `ChatGuardrails.sanitize_output(response)` — redacts internal IPs, hostnames,
  private keys, secrets from model output (defense in depth).
- `ChatGuardrails.build_safe_messages(system_prompt, history, user_message)` —
  builds a message list that can never smuggle a system role.
- `get_chat_rate_limiter()` → `ChatRateLimiter` (30/min, 200/hour per client).

Wiring in the bot (both `cmd_chat` and the catch-all `handle_message` AI branch):

```python
guardrails = ChatGuardrails()

async def _llm_chat(user_id: int, text: str, token_ctx: str | None = None) -> str | None:
    allowed, safe_text = guardrails.sanitize_input(text)
    if not allowed:
        guardrails.record_block(str(user_id), text, reason=safe_text, category="input")
        return "⛔ I can't help with that request."
    system_prompt = build_chat_system_prompt(token_ctx)   # includes RAG context
    messages = guardrails.build_safe_messages(system_prompt, [], safe_text)
    async with httpx.AsyncClient(timeout=45) as client:
        r = await client.post(LLM_CHAT_URL, json={
            "model": LLM_MODEL, "messages": messages, "temperature": 0.3,
            "max_tokens": 600, "stream": False,
        })
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"]
    return guardrails.sanitize_output(raw)
```

- **RAG bridge context:** before calling the LLM, for messages containing a token
  address, call `POST /api/v1/rag/v2/search` (collection `scam_intel`, top_k 3) and inject
  the top hits into the system prompt as `CONTEXT (scam intelligence)` so Qwen answers
  grounded in RMI data. If RAG is down, proceed without context (never block chat).
- **Rate limits:** per-user weekly `db.check_rate_limit(user_id, "ai_msg")` AND the
  shared `ChatRateLimiter` (per user id) — double protection.
- **Quotas:** `db.increment_usage(user_id, ai_msg=True)` after a successful completion.
- **Fallbacks:** if llama-server is down → existing smart fallback responses (honeypot/
  price/help keywords), never a raw 500 to the user.
- **`/analyze` command:** dedicated handler that runs `scan_token_v2` (quick) first,
  feeds the machine-readable scan result + RAG hits into the LLM, and returns a
  plain-language risk verdict with the standard scan-result inline buttons.

---

## 6. Part 4 — Automated Alerts Channel

### 6.1 Alert types & sources

| Alert type | Trigger source | Push path |
|---|---|---|
| High-risk token | RugMunchScan result `risk_tier in (high, critical)` OR severe flag (honeypot/rugpull/LP removed) | bot `alerts` pipeline |
| Exploit / incident | Backend `mempool_sentinel` / threat intel events | `POST /api/v1/alerts/webhook` → bot |
| Whale movement | Backend intelligence `get_whale_movements` | bot polling job or webhook |
| Price alerts | existing watchlist `check_alerts` job | existing job queue |
| News alerts | rated news feed matches user topic prefs | hourly news job |

### 6.2 Subscription model (`/alerts on|off [category]`)

DB table addition (`db.py`):

```sql
CREATE TABLE IF NOT EXISTS alert_subscriptions (
    user_id INTEGER NOT NULL,
    category TEXT NOT NULL,          -- security | whale | price | news_defi | news_security
    channel_id INTEGER NOT NULL,     -- DM or channel id
    is_active INTEGER DEFAULT 1,
    created_at INTEGER,
    PRIMARY KEY (user_id, category)
);
```

Command syntax:
- `/alerts` — show current subscriptions + toggle buttons
- `/alerts on security` / `/alerts on whale` — subscribe
- `/alerts off` / `/alerts off security` — unsubscribe
- Premium gate: `security` + `whale` require Scout+; `price` requires Scout+ (existing);
  `news_*` free.

### 6.3 Push pipeline (backend → bot)

The 501 stub at `POST /api/v1/alerts/webhook` in
`rmi-backend/app/api/v1/admin/alerts_webhook.py` must be implemented, OR the bot exposes
its own webhook endpoint. [REVIEW] — recommend implementing the backend webhook with an
HMAC `X-Alert-Secret` header; bot polls it via a `JobQueue` job every 30s as a fallback
if webhook delivery is not available (polling keeps the bot stateless).

Bot side — new module `handlers/alerts.py` (extend existing):

```python
async def alert_delivery_job(context: ContextTypes.DEFAULT_TYPE):
    """Poll backend alert queue; fan out to subscribers (max 5/hour/user)."""
    alerts = await fetch_pending_alerts()          # GET /api/v1/intelligence/my-alerts or webhook queue
    for alert in alerts:
        for sub in db.get_alert_subscribers(alert["category"]):
            if rate_limited(sub["user_id"]):        # 5/hour cap per user
                continue
            await context.bot.send_message(
                sub["channel_id"], format_alert(alert), parse_mode=ParseMode.HTML,
                reply_markup=alert_kb(alert),
            )
```

- **Alert formatting** — severity emoji prefix (🟢/🟡/🟠/🔴/🆘), token, risk score bar,
  short reason, inline **🔍 Scan · 🤖 AI · 📊 Chart** buttons.
- **Anti-spam:** hard cap **5 alerts/hour/user** (project rule); dedupe by
  `(token_address, alert_type)` within 30 min; `Forbidden` → unsubscribe quietly.
- **Channel vs DM:** subscribers receive in-DM; a broadcast mirror posts critical
  (`critical`) alerts to `CHANNELS["alerts"]` (`@rmialerts`, `-1003818352164`).
- **Alert hooks:** wire the existing dead-code `alert_hooks.send_scanner_alert()` so the
  scanner subsystem can push directly (TODO.md HIGH priority fix).

### 6.4 Dedup / state

`db.py` additions:
- `alert_subscriptions` (above)
- `alert_sent_log (token_address, alert_type, sent_at)` — dedupe window
- per-user hourly counter for the 5/hour cap (Redis preferred; in-memory TTLCache fallback)

---

## 7. Part 5 — News Channel

### 7.1 Source

`GET /api/v1/internal/news-feed?channel=news&limit=5&min_score=0` (live, rated):
returns `items[]` with `title`, `source`, `published_at`, `url`, `bias`,
`sentiment_score`, `factual_rating`, `source_tier`, `quality_score`.

### 7.2 `/news [topic]` command

```
/news        → top rated (sort=impact, 5 items)
/news defi   → filter by topic/defi tag
/news security → security incidents
```

Format (reuse live `cmd_news` styling — bias/sentiment/factual emojis):

```
📰 Rated Crypto News  ·  via RMI News Network
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔥 ⚪ [★★★★] Title of the article…  · source · 2h ago
  Factuality: High
❄️ 🔵 [★★★] Another headline…  · source · 5h ago
  Factuality: Medium
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌐 More on rugmunch.io · 📢 @rmialerts
```

Topic filtering [REVIEW]: backend feed has no topic param today; either (a) add
`topic` filter to `news_feed.py`, or (b) client-side keyword filter on title/tags.

### 7.3 Hourly auto-post

`JobQueue` job `hourly_news_post` (top of every hour):

```python
async def hourly_news_post(context: ContextTypes.DEFAULT_TYPE):
    articles = await fetch_rated_news(limit=3)   # min_score=60, sort=impact
    for a in articles:
        await context.bot.send_message(CHANNELS["news"], format_news_post(a),
                                       parse_mode=ParseMode.HTML,
                                       disable_web_page_preview=True)
```

- Posts to `CHANNELS["news"]` (`@rmicryptonews`, `-1003982391578`).
- **Anti-duplicate:** `news_feed.py` already tracks `_posted` URLs server-side
  (`news-feed` marks items as posted) — use that endpoint so auto-posts never repeat.
- Cooldown: max 3 posts per hour, skip if feed empty or backend error (silent retry next hour).

---

## 8. Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                        Telegram Bot (@rugmunchbot)               │
│  handlers/  scanning · deepscan · analyze · risk · market ·      │
│             alerts · news · chat · message(catch-all) · callback │
│  scanner.py  scan_token_v2() ─┐  _llm_chat() ──────┐            │
│  alert_hooks.py  ─────────────┤                    │            │
│  job_queue: hourly_news · alert_delivery · watchlist│            │
└────────┬──────────────┬───────┴────────┬───────────┴─────────────┘
         │              │                │
         ▼              ▼                ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────────────┐
│ RugMunchScan API│ │ llama-server    │ │ RMI RAG bridge          │
│ POST /api/v2/   │ │ POST :8093/v1/  │ │ POST /api/v1/rag/v2/    │
│ scanner/token/  │ │ chat/completions│ │ search (scam_intel)     │
│ scan (→v1 poll) │ │ qwen3.5-9b      │ │ → top_k context          │
└────────┬────────┘ └─────────────────┘ └─────────────────────────┘
         │
         ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────────────┐
│ News feed       │ │ Market intel    │ │ Alert webhook           │
│ GET /api/v1/    │ │ /market/        │ │ POST /api/v1/alerts/    │
│ internal/       │ │ fear-greed,     │ │ webhook (HMAC)          │
│ news-feed       │ │ overview        │ │ → alert_delivery_job    │
└─────────────────┘ └─────────────────┘ └─────────────────────────┘
```

Chat guardrails (`ChatGuardrails` + `ChatRateLimiter`) sit **between** the bot handlers
and llama-server (input sanitize → classify → LLM → output sanitize).

---

## 9. Security & Abuse Controls

| Risk | Control |
|---|---|
| Prompt injection | `ChatGuardrails.sanitize_input` + `build_safe_messages` (delimiter escape) |
| Internal info leak in output | `sanitize_output` redaction of IPs/hostnames/keys |
| Chat spam | `ChatRateLimiter` (30/min, 200/hr) + per-user weekly AI quota |
| Command flood | existing `check_spam` 3s cooldown; owner whitelist |
| Alert spam | 5 alerts/hour/user cap, dedupe window, `Forbidden` auto-unsubscribe |
| Malformed input | EVM/SOL regex validation before any API call; friendly <2s errors |
| Secrets | no API keys in code — `X-API-Key` from env (gopass); webhook HMAC secret via env |
| Webhook spoofing | `X-Alert-Secret` HMAC validation on `/api/v1/alerts/webhook` |

---

## 10. Implementation Task Breakdown

Ordered build plan (code lands in canonical live bot; port to standalone after):

1. **Config** — commit `LLM_CHAT_URL` / `LLM_MODEL` (already staged uncommitted in
   standalone; add to live `config.py`); add `ALERT_WEBHOOK_SECRET`, `NEWS_CHANNEL_ID`.
2. **DB** — `alert_subscriptions`, `alert_sent_log` tables + helpers.
3. **Scanner v2 client** — `scan_token_v2()` with v1 fallback in `scanner.py`;
   extend `format_scan_report()`; `/deepscan` + premium gate.
4. **AI chat** — `_llm_chat()` + guardrails wiring; `/chat`, `/analyze`; RAG context
   injection; update `handle_message` catch-all to use LLM before fallback.
5. **Alerts** — implement backend webhook (or bot polling job); `/alerts on|off`;
   `alert_delivery_job`; wire `alert_hooks.py`.
6. **Market** — `/market` (fear-greed + overview + movers).
7. **News** — extend `/news [topic]`; `hourly_news_post` job.
8. **Menu** — update `main_menu_kb()` + callback handlers; `set_my_commands`.
9. **Tests** — respx-mocked tests for scanner v2 fallback, guardrail blocks,
   alert dedupe/cap, news formatting. Target coverage ≥60% on new modules.
10. **Docs** — this document updated with shipped status.

**Dependencies:** 1 → 2 → 3/4 can run after; 5 depends on 2 + webhook decision;
6/7 depend on backend endpoints (market/news already live).

---

## 11. Competitor Comparison

| Capability | **RugMunchBot V3** | Banana Gun | Maestro | Trojan | aixbt |
|---|---|---|---|---|---|
| Token security scan | ✅ RugMunchScan + DataBus | ⚠️ basic honeypot check | ⚠️ basic | ⚠️ basic | ❌ |
| Deep forensic (SENTINEL) | ✅ planned (premium) | ❌ | ❌ | ❌ | ❌ |
| Rug-pull / bundle detection | ✅ yes (DataBus bundle) | ❌ | ❌ | ❌ | ❌ |
| AI chat (local Qwen3.5-9B) | ✅ free, on-prem | ❌ | ❌ | ❌ | ✅ (aixbt agent) |
| RAG-grounded answers | ✅ scam_intel KB | ❌ | ❌ | ❌ | ⚠️ social only |
| Wallet forensics | ✅ existing | ❌ | ❌ | ⚠️ copy-trade focus | ⚠️ |
| Rated news feed | ✅ bias/sentiment/factual | ❌ | ❌ | ❌ | ✅ (alpha feed) |
| Market fear & greed | ✅ /market | ❌ | ❌ | ❌ | ✅ |
| Automated alerts | ✅ security/whale/price | ⚠️ snipes only | ✅ signals | ✅ signals | ✅ |
| Trading/sniping | ❌ (intel-only, safe) | ✅ core | ✅ core | ✅ core | ❌ |
| Monetization | Stars + tiers + DEX ref | fee-on-trade | fee-on-trade | fee-on-trade | sub/alpha |

**Positioning:** RugMunchBot V3 is the only **security/intelligence-first** bot —
trading bots (Banana Gun/Maestro/Trojan) optimize execution; aixbt optimizes social
alpha. RMI owns the **"don't get rugged"** quadrant: on-chain forensics, SENTINEL deep
scans, rated news, guardrailed local AI — with DEX referral revenue as the monetization
bridge, no custody, no trading risk.

---

## 12. Open Questions / [REVIEW] Items

1. **Tree of record** — which codebase receives V3 code first (live canonical vs
   standalone)? This doc is written to be implementable in either.
2. **v2 scanner endpoint** — `POST /api/v2/scanner/token/scan` does not exist yet;
   v1 (async + poll) is live. Confirm v2 contract with backend owner before coding
   the synchronous path.
3. **Alert delivery mechanism** — backend webhook (needs 501 stub implemented) vs bot
   polling job. Recommend webhook + polling fallback.
4. **News topic filter** — backend feed has no `topic` param; client-side filtering
   acceptable for v1.
5. **AGENTS.md conflict** — standalone repo still says "orphan / no new features";
   ARCHIVED.md + TODO.md reversed it. Recommend updating AGENTS.md to reflect the
   TOP-TIER status (separate docs commit).

---

*Design doc: RugMunchBot V3 — scanner, SENTINEL, menu, alerts, news. 2026-08-04.*
