# RUGMUNCHBOT — FULL TEARDOWN & 50 WINS (2026-08-25)

Single-agent audit: code paths traced, production logs mined (24h), channel
routing logic read line-by-line, RAG integration mapped end-to-end.

---

## PART 1 — HOW THE BOT WORKS TODAY (as-built)

### RAG integration map (what exists)
| Path | Status |
|---|---|
| TG news sweep → cluster → `ingest_into_rag()` → news_v2 | **DEAD** — 1,181 ingest failures/6h |
| Chat answers → RAG (`get_news` tool) | **DEGRADED** — `rag_count=0` most answers; primary news_v2 "empty" → legacy fallback |
| Content drafts ← rugbrain `/api/v1/content/drafts` | Working (2 posted/3h) |
| Importance rerank | Working (bge-reranker via backend) |
| Story dedupe | Working (clusterer + chunk-hash `rag:hashes:news_v2`) — but see #W6 |

### Channel routing (as coded in `channels/router.py`)
```
importance ≥ 0.7            → main (@cryptorugmuncher, ≤9/day cap)
category == alpha           → alpha
alert type: CRITICAL/HIGH   → alerts (@rmialerts); lower → SKIP
scan/shill/whale            → native lane (scans/shills/alpha)
0.4 ≤ importance < 0.7      → news (@rmicryptonews)
< 0.4                       → skip
main overflow               → demote to native lane
```
**Gap**: no explicit "major update → cryptorugmuncher" rule for exploits/hacks
unless importance ≥0.7; alerts never escalate to main; no link-required rule.

### The failure cascade (root cause chain)
```
Migration moved collections to native 2560 (qwen3-embedding-4b)
        ↓
Bot's get_embedding() → rugbrain /api/v1/embed → returns 1024 (old
COLLECTION_DIM MRL policy inside rugbrain embeddings.py)
        ↓
Upsert 1024-vec into 2560 collection → "array dimensions... 1024 vs 2560"
        ↓
ingest_doc_failed ×1181 · news_v2 stays empty for NEW news
        ↓
get_news searches empty primary → legacy fallback (stale data)
        ↓
chat answers with rag_count=0 · no citations from our KB
        ↓
feed.rugmunch.io/data/posts.json EMPTY (mirror never fires)
```

---

## PART 2 — THE FUCK-UPS FOUND (evidence-backed)

| # | Fuck-up | Evidence |
|---|---|---|
| F1 | News→RAG writes 100% dead (dim mismatch 1024 vs 2560) | `ingest_doc_failed` ×1181/6h |
| F2 | Chat times out at 60s, user gets nothing | `route:"timeout" ok:false latency:60003` |
| F3 | Chat ignores RAG (rag_count=0) even when KB has data | chat_answer_trace ×3/4 |
| F4 | Postgres persistence skipped EVERY sweep | `persist_skipped_postgres_unavailable` ×9 |
| F5 | feed.rugmunch.io serves EMPTY posts.json | file = 16 bytes; 0 mirror events/24h |
| F6 | Photo posts fail: "Message caption is too long" | channels:send_photo_failed ×2+ |
| F7 | Admin cards crash: unescaped HTML ("unsupported start tag magicmock") | BadRequest traceback ×2 |
| F8 | scam_watch verdicts EMPTY: `safety=None flags=[]` on every scan | scan_done events |
| F9 | Scan commentary capped at 4s/200 chars — garbage commentary | _COMMENTARY_TIMEOUT=4.0 |
| F10 | market_awareness LLM output = 8 tokens (truncated/broken gen) | `tokens=8` per 200 articles |
| F11 | `KNOWN_DIMS` duplicate dict key (1024 then 2560) — silent override | embedder.py:47-48 |
| F12 | Doc rejection storm: `spam_repetitive` ×1089/6h — gate miscalibrated | doc_rejected events |
| F13 | Slow scans: 41.8s / 39.4s latencies on scan ops | chat_answer_trace/latency |
| F14 | rpc_endpoint_dead events — RPC pool degrading | ×4/6h |
| F15 | Solscan/CMC/Etherscan keys expired (known gap, still unfixed) | ARCHITECTURE.md Known Gaps |

---

## PART 3 — 50 WINS TO MAKE IMMEDIATELY

### A. UN-BREAK THE RAG PIPELINE (P0 — today)
1. **W1**: Fix bot embed dim: rugbrain `/api/v1/embed` must accept
   `?dim=2560` (or read target collection); bot passes collection dim per
   ingest. Unblocks ALL of news→RAG.
2. **W2**: Kill duplicate `KNOWN_DIMS["qwen3-embedding-4b"]` key; single
   registry import from dim_policy truth.
3. **W3**: After W1: backfill last-48h swept stories into news_v2 (state
   file has them; re-run sweep with ingest enabled).
4. **W4**: Add ingest success metric + alert: `ingested=0 errors>100` pages.
5. **W5**: Fix Postgres persistence (F4): point bot at working DSN or drop
   gracefully to SQLite; persistence is the dedupe backbone across restarts.
6. **W6**: Verify chunk-hash dedupe keyspacing after dim fix (hash includes
   model+dim so old 1024 hashes don't suppress new 2560 chunks).
7. **W7**: Wire SCAN results → RAG ingestion (scans currently never hit
   RAG): scan verdicts → `scanned_tokens` collection docs w/ token address
   metadata. Scans become searchable knowledge.
8. **W8**: Wire ALERT verdicts → RAG (`threat_intel_v2_i` incremental):
   every CRITICAL/HIGH alert becomes a threat-intel doc.
9. **W9**: Ingest dedup: before embed, query existing doc_id; skip if
   identical content-hash (already built) AND log skip reason distinctly.
10. **W10**: Nightly reconciliation job: count stories swept vs ingested;
    delta >5% → auto-backfill + alert.

### B. CHAT: ANSWERS + CITATIONS (P0 — today/tomorrow)
11. **W11**: Fix 60s chat timeouts: route-level budget 25s hard; on
    timeout return partial + "sources consulted" instead of nothing.
12. **W12**: Force RAG consult: every analysis-intent question MUST call
    get_news/get_knowledge before LLM; rag_count=0 on analysis intent =
    bug alert.
13. **W13**: Citation contract: every factual claim in answers carries
    [source: title](url) from RAG payloads; no-source claims get
    "(unverified)" tag. Anti-hallucination guard already exists — extend
    to require ≥1 citation when sources were used.
14. **W14**: When news_v2 primary is empty → say so honestly: "KB coverage
    limited to {date}; here's web-grounded answer" instead of silent legacy.
15. **W15**: Chat latency: cache LLM warm connection pool; stepfun free
    route took 23-31s — add streaming so first tokens land <3s.
16. **W16**: Timeout tiering: fast intent → 15s budget; analysis → 45s +
    progress message at 10s ("digging through KB and web…").
17. **W17**: Log + expose `rag_count`, `citations[]` in every answer footer
    ("Grounded in N sources") — already exists for web; extend to RAG hits.

### C. CHANNEL STRATEGY & CONTENT QUALITY (P1 — this week)
18. **W18**: Routing rule addition: `exploit/hack/rug-confirmed` categories
    with severity HIGH+ ALWAYS escalate to main (@cryptorugmuncher)
    regardless of importance score — major events can't be missed.
19. **W19**: Main-channel link contract: no post without ≥1 link (article,
    dexscreener, scanner). Enforce at send time; reject+log violations.
20. **W20**: Main-channel commentary contract: every main post = headline +
    1-2 sentence OUR take (LLM-generated, fact-checked vs source) + link.
21. **W21**: Raise commentary budget: `_COMMENTARY_TIMEOUT` 4s→15s,
    max_chars 200→400 for main/alpha; keep 4s only for low-tier lanes.
22. **W22**: Fix caption-too-long: truncate-to-1024 with ellipsis + full
    text as reply; never drop the post (F6).
23. **W23**: Fix admin-card HTML escaping: `html.escape()` all interpolated
    log lines (F7).
24. **W24**: market_awareness generator: investigate tokens=8 (prompt or
    max_tokens bug); require ≥40-token summary or skip publishing.
25. **W25**: Alerts channel doctrine enforcement: ONLY coins likely to rug
    (rug_prediction ≥ threshold), active exploits/hacks, verified scams.
    Add negative filter: price-movement-only items NEVER hit alerts.
26. **W26**: Exploit/hack fast-path: sec feeds (Rekt, DefiLlama exploits,
    security_feeds collection) → immediate alerts + main escalation within
    5 min of detection, bypass importance queue.
27. **W27**: Dedup calibration: `spam_repetitive` ×1089 — sample 50
    rejected docs; if >30% are unique stories, loosen similarity threshold
    (was 0.6→0.15 shift; recalibrate against current corpus).
28. **W28**: Per-channel style guides in prompts: news=factual+link,
    alerts=verdict+risk-score+action, main=commentary+link, alpha=degen
    tone allowed. One prompt template per channel in config.
29. **W29**: Quality bar: min story length, must have date+source, kill
    "just happened" with no substance. Reject at importance stage not send.
30. **W30**: Feed mirror repair: fire `_mirror_to_feed` on every main post
    (currently 0 fires/24h); verify FEED_DIR writable + JSON schema matches
    degenfeed-web reader; backfill last 30 days of main posts.

### D. SCANS: SPEED + VERDICTS (P1 — this week)
31. **W31**: Fix scam_watch empty verdicts: `safety=None flags=[]` means
    classifier never ran — trace classify path, restore verdict population.
32. **W32**: Scan latency: 40s p95 → target <15s: parallelize RPC phase
    groups, cache token metadata 5min, stream partial results to user.
33. **W33**: Progress UX: scan started → inline "checking holders… LP…
    deployer…" edits every 5s (progress module exists — wire to deep scan).
34. **W34**: RPC endpoint deaths: rotate-dead-instantly (4 dead events);
    auto-deprioritize 30min on first death, health-check before reuse.
35. **W35**: Regenerate Solscan/CMC/Etherscan keys (expired per audit);
    add expiry monitor warning 7 days before.
36. **W36**: Scan results → channel quality: verdict card MUST include
    risk score, top 3 red flags, links (dexscreener + full scan URL).
37. **W37**: Scan→RAG loop closes W7: repeated scans of same token update
    one doc (doc_id=scan:{chain}:{address}) — knowledge compounds.

### E. RELIABILITY & OBSERVABILITY (P1)
38. **W38**: Single health dashboard: sweeps, ingest success %, chat
    timeout %, mirror freshness, cap usage — one status card for admins.
39. **W39**: Error-budget alerts: any event-family >N/minute (like
    ingest_doc_failed 1181) auto-pages via rmiadminbot.
40. **W40**: Restart-resilience: scheduler_state already persists — add
    ingest state (last successful RAG write ts) to skip re-sweep gaps.
41. **W41**: Rate-limit hygiene: OpenRouter free routes (stepfun/nemotron)
    — add 429 backoff + provider rotation already in llm_config; verify
    rotation actually triggers under load.
42. **W42**: Test the pipeline end-to-end nightly: synthetic story →
    sweep → ingest → search → cite; assert each hop (would have caught F1).

### F. CONTENT DOCTRINE (P2 — polish)
43. **W43**: Kill cross-post duplicates visually: same story to main+news
    gets distinct framing (main=commentary, news=factual) — never same text.
44. **W44**: Emoji/formatting standard per channel (alerts 🔴/🟡 severity,
    main 🧵 thread-style, news plain) — codify in formatters.
45. **W45**: Source diversity tracker: if >60% of week's main posts come
    from one source, rebalance lane weights.
46. **W46**: Alpha channel: whale moves + degen alpha with risk labels —
    never straight news copies.
47. **W47**: Weekly digest experiment: Sunday recap of top 10 stories w/
    outcomes (did the flagged coin rug?) — builds trust + closes loop.
48. **W48**: Link health: resolve all posted URLs; dead-link rate >5% →
    swap source.
49. **W49**: Feedback loop: reaction-based scoring (👍/👎 on main posts)
    feeds importance weights per category.
50. **W50**: Document CONTENT-STRATEGY-CANONICAL.md updates: new routing
    rules (W18-W21), link contract, tier doctrine — single canonical doc.

---

## PART 4 — INTEGRATION PLAN (RAG ↔ BOT, complete loop)

```
                    ┌──────────────────────────────┐
   TG sources ──►   │ SWEEP (telegram_news)        │
                    │  ├─ cluster → stories        │
                    │  ├─ importance score         │
                    │  ├─ route (router.py matrix) │
                    │  │   ├─ alerts (CRIT/HIGH)   │──► @rmialerts
                    │  │   ├─ news                 │──► @rmicryptonews
                    │  │   ├─ main (+commentary)   │──► @cryptorugmuncher
                    │  │   └─ alpha                │──► alpha chan
                    │  ├─ ingest_into_rag (FIX W1) │──► news_v2 (2560)
                    │  ├─ persist postgres (FIX W5)│──► dedupe backbone
                    │  └─ mirror main (FIX W30)    │──► feed.rugmunch.io
                    └──────────────────────────────┘
   Scanner ──► verdict cards ──► scans channel ──► scanned_tokens (W7/W37)
                     │
                     └─► CRITICAL/HIGH ──► threat_intel_v2_i (W8)
   Chat ──► get_news/get_knowledge (W12) ──► citations (W13/W17)
   Consumer search ──► federated multi-space ──► rerank 4B (:8003)
```

Every producer feeds RAG; every consumer reads RAG with citations; every
channel has an enforced content contract.

## Execution order
Day 1: W1,W2,W11,W18,W19,W22,W23 (unblock pipeline + stop bleeding)
Day 2: W3,W5,W7,W8,W12,W13,W25,W26,W30,W31
Week: rest, in order, measuring via W38 dashboard.
