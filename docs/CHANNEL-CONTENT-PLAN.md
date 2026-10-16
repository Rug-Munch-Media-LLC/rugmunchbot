# CHANNEL CONTENT PLAN — every channel, every source (2026-10-17)

> Canonical. Companion to `CONTENT-STRATEGY-CANONICAL.md` (the doctrine) and
> `src/rugmunchbot/channels/content_charter.py` (the contract). This document
> answers the two questions the charter alone does not: **what each channel
> covers** and **exactly where its content comes from**.

Two machine-readable sources of truth, both enforced by tests:

| Question | Source of truth | Test |
|---|---|---|
| Which content types may a channel post? | `channels/content_charter.py` (`CHANNEL_CHARTER`, `channel_accepts_content`) | `tests/test_channel_charter.py` |
| Where does each content type's data come from? | `channels/content_sources.py` (`CONTENT_SOURCES`) | `tests/test_content_sources.py` |

A content type with no declared source is a channel nobody can feed — the
defect class that left `@rmialpha` / `@rmiscans` quiet. `test_content_sources.py`
asserts every canonical type has a source, so a new type cannot ship unwired.

## 1. Per-channel plan

| Channel | Handle | Purpose | Content types | Sources | Cadence | Graphic |
|---|---|---|---|---|---|---|
| `main` | @cryptorugmuncher | Flagship — top-tier anything + escalations from every lane | market, roundup, news, alert, scan, whale, alpha, shill, scam_alert | all lanes (escalations) | event-driven + daily wrap; ≤10/day (`main_cap`) | per type (see §3) |
| `news` | @rmicryptonews | Breaking crypto news ONLY — sourced, dated, linked | news | `news_v2` (TG sweep + rugbrain rail) | event-driven + scheduled sweeps | `rmi-breaking-news.jpeg` |
| `alerts` | @rmialerts | Security alerts ONLY — hacks/exploits/rugs/scams | alert, scam_alert | scanner `/alerts/recent`, exploit drafts, `scam_patterns`, `threat_intel_v2` | event-driven + regular digest | alert→`current_cryptorugmuncher.png`; scam_alert→`rmi-scam-alert.jpeg` |
| `alpha` | @rmialpha | Whale + smart-money signals ONLY (own data) | whale, alpha | ClickHouse `wallet_memory.token_transfers` / `deployer_events`, `smart_money_feed` | scheduled sweeps (30 min) | whale→`rmi-whale-alert.jpeg`; alpha→`current_cryptorugmuncher.png` |
| `scans` | @rmiscans | Suspicious-token scans ONLY | scan | `/api/v1/launches/alerts` + `/api/v1/data/scan-history` | ~10/day | `current_cryptorugmuncher.png` |
| `updates` | @rmiupdates | Product updates / release notes ONLY | update | `_probe_services()` health probe + release notes | daily digest | `rug-munch-media-llc.png` |
| `shills` | @rmishills | Shill-campaign intel ONLY | shill | rugbrain `/rag/shill-campaigns`, KOL sweep, `threat_intel_v2` | event-driven | `current_cryptorugmuncher.png` |

## 2. Data flow — per content type

Each row is the four-stage path: **ingest → enrich → rerank → render**. The
machine-readable form is `CONTENT_SOURCES` in `channels/content_sources.py`.

| Type | Ingest (source) | Enrich | Rerank / gate | Render |
|---|---|---|---|---|
| `market` | CoinGecko prices + global metrics + trending/movers; rugbrain news-rerank over `news_v2` | RAG intel lanes: `news_v2` + `threat_intel_v2` + `whale_transfers` + `hacks_v2` + `market_health` + `regulation_enforcement` | rugbrain consumer reranker `/api/v1/rag/news-rerank` (24h) | `daily_market_summary/_fmt` + `daily_rug` longform |
| `roundup` | the day's already-posted stories (seen-store) | dedupe vs the day's posted set | importance rerank | `compose_roundup` |
| `news` | TG sweep (Telethon) → `news_v2`; fallback rugbrain `news_v2` + `/api/v1/news/trending` | quality filter → rate limiter → chunk → embed; canonical-URL dedupe | `bge-reranker-v2-m3` importance (floor 0.4, main 0.7) | `telegram_news/_digest` |
| `alert` | scanner `/api/v1/alerts/recent`; exploit `exploit_drafts.json`; `/api/v1/launches/alerts` + Redis `rmi:alerts:recent` webhooks | entity labels + severity + top-3 red flags + source link | importance rerank (alerts 0.4, main 0.7) | `format_alerts_post` / `format_main_escalation` |
| `scan` | `/api/v1/launches/alerts` (>70/100) merged with `/api/v1/data/scan-history` (Postgres `scan_results`) | `_enrich_missing_identities` + dedupe by address | risk-score sort + `_scan_content_type_gate` | `format_scan_card` |
| `whale` | ClickHouse `wallet_memory.token_transfers` ≥ $10k; `deployer_events` early entries | entity labels (`wallet_memory.entity_labels`) + size + chain | HARD quality gate (paid-product) | `build_whale_post` |
| `alpha` | `smart_money_feed` (`smart_money_wallets`) + `/api/v1/launches/feed` + SearXNG `kol_trades` | entity labels + PnL + source link | importance rerank (alpha category) | `format_alpha_post` |
| `shill` | rugbrain `/rag/shill-campaigns` (3+ KOLs, same token, 24h); KOL sweep | X screenshot + OCR (VL-4B) → `shill_evidence` + `threat_intel_v2`; scanner evidence | importance rerank (main 0.7, alerts 0.4) | `format_shill_alert` / `build_shill_campaign_post` |
| `scam_alert` | `/var/lib/rugbrain/shill_alerts.jsonl` | severity from risk (0-5) + addresses/tickers/categories + verify link | risk sort + rugbrain rerank tie-break (`scam_patterns`) | `format_scam_alert` |
| `update` | `_probe_services()` health probe + release notes | health-line assembly | `channel_accepts_content('updates','update')` | `compose_updates_digest` |
| `system` | ops/health probes | status assembly | none (informational) | system notification |

### RugBrain collections referenced

`news_v2` · `social_intel` · `threat_intel_v2` · `scam_patterns` · `scanner_memory` ·
`whale_transfers` · `hacks_v2` · `market_health` · `regulation_enforcement` ·
`telegram_social`. The `_8b` suffix is resolved server-side; the API accepts the
base name (`channels/content/_rag_news.py`).

## 3. Routing rules — no channel posts off-charter content

The gate is `channel_accepts_content(channel, content_type)` in
`content_charter.py`, enforced at the single send chokepoint
`scheduler/_core.py::_enforce_content_contract` (every `_safe_send` passes
through it). Fail-soft: an unknown channel or empty type returns `True` so a
mislabeled caller can never silently drop a post — the gate only blocks a
**known** channel posting a **known** type it is not chartered for.

Defense-in-depth gates on top of the chokepoint:

- `_posts_scan.py::_scan_content_type_gate` — `@rmiscans` blocks news / whale /
  system / update / shill kinds and any item with no token address.
- `_posts_news.py` — `@rmialerts` refuses to post if it is ever misconfigured to
  accept a non-alert type.
- `_posts_updates.py` — `@rmiupdates` refuses non-update content.
- `_posts_rag/_main.py::_news_content_type_gate` — `@rmicryptonews` blocks
  whale/scan/system/roundup drafts.

## 4. Graphics

`support/post_graphics.py::POST_TYPE_GRAPHICS` is the single map; the charter
reaches it via `ChannelCharter.graphic_for(type)`, so the charter and the poster
can never disagree on the image. Operator originals: `rmi-scam-alert.jpeg`,
`rmi-breaking-news.jpeg`, `rmi-whale-alert.jpeg`, `rmi-evening-market-wrap.jpeg`,
`daily-rug-logo.jpeg`, `current_cryptorugmuncher.png`, `rug-munch-media-llc.png`.

## 5. Verification

```
heavy-run python3 -m pytest tests/test_content_sources.py tests/test_channel_charter.py -q
  → 34 passed
```
