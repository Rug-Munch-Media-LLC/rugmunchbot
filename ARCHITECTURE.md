# rugmunchbot — Architecture

> Last updated: 2026-08-27 · 593 files · 144K lines · 1174 tests green
> The RugMunch Intelligence Telegram bot + proactive channel network.

## Sequence Diagrams

### Scan Flow

```
User → /scan 0xabc
  │
  ├─→ handlers/_scan_handlers/_scan.py::cmd_scan()
  │     │
  │     ├─→ tools/scanner/_scans.py::scan_token()
  │     │     │
  │     │     ├─→ POST /api/v1/scanner/token/scan (rmi-backend)
  │     │     │     │
  │     │     │     ├─→ Contract safety checks
  │     │     │     ├─→ Liquidity analysis
  │     │     │     ├─→ Holder distribution
  │     │     │     ├─→ Deployer profiling
  │     │     │     └─→ Risk scoring
  │     │     │
  │     │     ├─→ GMGN enrichment (smart money)
  │     │     ├─→ Solana on-chain ground truth
  │     │     └─→ schedule_scan_memory_index() → Qdrant (background)
  │     │
  │     └─→ format_scan_report() → Telegram
  │
  └─→ User receives formatted scan report
```

### RAG Health Check at Startup

```
bot/_entry.py::startup()
  │
  ├─→ check_backend_health()
  │     └─→ GET /health → backend
  │
  ├─→ _check_rag_health()
  │     │
  │     ├─→ run_health_checks()
  │     │     ├─→ Qdrant health probe
  │     │     └─→ Embedder health probe
  │     │
  │     └─→ set_rag_available(True/False)
  │
  ├─→ _check_scanner_health()
  │     └─→ get_rpc_router().get_healthy_endpoint("ethereum")
  │
  └─→ setup_bot_profile() → assert_command_menu()
```

### Webhook Delivery Flow

```
Event detected (price change, whale move, etc.)
  │
  ├─→ channels/scheduler/_posts_*.py
  │     │
  │     ├─→ record_webhook_event(event_type)
  │     │
  │     ├─→ filter_event_by_subscription()
  │     │     ├─→ Chain filter
  │     │     ├─→ Token address filter
  │     │     ├─→ Wallet address filter
  │     │     └─→ Min value filter
  │     │
  │     ├─→ batch_events() (if batching enabled)
  │     │
  │     ├─→ generate_webhook_signature(payload, secret)
  │     │
  │     └─→ POST subscriber_url (with retry + exponential backoff)
  │           │
  │           ├─→ Success → record_webhook_delivery(success=True)
  │           │
  │           └─→ Failure → retry (10s, 20s, 40s, 80s, 160s)
  │                 │
  │                 ├─→ Max retries exceeded → DeadLetterQueue.add()
  │                 │
  │                 └─→ record_webhook_delivery(success=False)
```

## System Overview

```
Telegram (users/channels)
    │
    ▼
bot/_entry.py ─── PTB Application (polling)
    ├── handlers/          ← command + callback routing
    ├── tools/             ← intelligence tools (registry pattern)
    ├── channels/          ← proactive content pipelines
    ├── _vendor/           ← scanner engines, RAG, OSINT (first-party patches)
    └── config/            ← first-party settings (vendor-refresh-proof)
```

## Core Subsystems

### Bot Entry (`bot/_entry.py`)
Single PTB Application. Registers all handlers, jobs, and lifecycle hooks.
`startup()` asserts backend health, bot profile, command menu (win 72),
and SIGTERM shutdown handlers.

### Handler Layer (`handlers/`)
Thin Telegram render layer over the tool registry. Commands own presentation;
tools own analysis. Key groups:
- `_scan_handlers/` — /scan + deep-scan flows
- `chat_ai/` — LLM chat with web-search grounding (`_llm/_sources.py`,
  `_guards.py` anti-hallucination verification)
- `integrity_handlers.py` — /volume /exchangeaudit /dune (win 26+27 cards)
- `liqmap.py`, `hlwhales.py`, `dislocation.py`, `status.py` — market intel
- `alerts/` — risk alert routing

### Tool Registry (`tools/__init__.py` + `registry.py`)
Named async tools with timeout/retry/TTL-cache. Every tool returns
`ToolResult(tool=, ok=, data=)`. Per-tool call counters (win 44).
Registered tools: volume_integrity, exchange_audit, dune_intel,
social_intel, funding_dislocation, cascade_watch, whale_convergence,
rug_prediction, security_scan, price_lookup...

### Content Channels (`channels/`)
Proactive posting pipelines with scheduler:
| Channel | Cadence | Pipeline |
|---|---|---|
| Daily Market Summary | daily AM | gather(10 lanes) → validate → rerank → LLM → publish |
| tg_news | routed | ingest → dedupe → importance → route(skip/news/main/alpha) |
| alerts | existing | scanner flags → verdict cards |
| dislocation | 2h | 7-venue funding → EXTREME filter → alert |
| social spikes | 90min | velocity+lean → narrative formation alert |
| scam_watch | scheduled | known-scam pattern monitoring |

**Content doctrine**: global story dedupe (cross-lane, restart-persistent
via Redis) → freshness gate (<48h, dated only) → 4B reranker selection →
sanity validation → LLM generation with anti-hallucination guards.

### Scanner Engines (`_vendor/scanner_engines/`)
First-party patched vendored code. Key modules:
- `rpc_router.py` — multi-provider RPC fallback (25 chains, free-first
  tiering, health tracking). Chain config in `config/rpc_chains.py`
  (FIRST-PARTY — vendor-refresh-proof).
- `own_holder_analysis.py` — ClickHouse holder data + Ankr Advanced
  mega-sampler fallback (300–500 wallets)
- `holder_mega_sampler.py` — paginated deep sampling (win: 10-50x market depth)
- `deployer_profiles.py` — repeat-deployer track records from ClickHouse
- `gmgn_client.py` / `gmgn_transport.py` — GMGN smart-money data

### Model Services (`llm_config.py` + external)
All inference via llama.cpp on Talos behind the gateway (:8010):
- Deep embed Qwen3-4B :8004 primary + DeepInfra API fallback (0.6B lane retired)
- Rerank 4B :8003 only (0.6B :8008 lane retired; never cloud)
- Chat 8B :8009 · Gateway auto-routes by word count + strain breaker
- Cloud fallback: OR MAIN (free light) / OR BACKUP (deep) — labeled degraded

### Data Sources (multi-source validated, free-first)
```
TIER 0: Own ClickHouse/Qdrant indexers
TIER 1: Free public RPC pool (~10/chain)
TIER 2: Keyed freemium w/ rotation:
        Ankr Advanced · BlockPI ×12 · Alchemy ×2 · dRPC · Helius ×2
TIER 3: Geo-blocked → Webshare proxy
TIER 4: Paid (DRPC PAYG) — last resort
```
Every source probed live; dead keys flagged; rotation automatic.

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Global story dedupe (not per-lane) | Same story must never post twice anywhere |
| Fail-open content pipeline | A broken lane never blocks the brief |
| Wash-gated simulations | Backtests on fake volume are lies |
| First-party config outside _vendor | Vendor refreshes can't wipe chain maps |
| Honest degradation labels | Cloud/degraded responses always flagged |
| Menu assertion on startup | Restarts can't silently regress UX |

## Known Gaps (production audit 2026-08-24)
- Content-pipeline test coverage: importance + titles pinned; router/news pending
- rpc_router 784L: logic/config split done; further module split optional
- Solscan/CMC/Etherscan keys: expired/session tokens — need regeneration
