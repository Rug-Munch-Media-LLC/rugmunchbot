# SCAN QUALITY & PERFORMANCE PLAN (2026-08-25)

> Canonical requirements for scanner output quality, caching, and fallback.
> Feeds CONTENT-STRATEGY-CANONICAL.md v2.

## TARGETS
| Metric | Now | Target |
|---|---|---|
| Regular /scan p95 | ~40s | <15s |
| Deepscan p95 | minutes | <60s with progress streaming |
| Cache hit rate | none | >40% within TTL |
| Data completeness | partial | every section present or honestly "unavailable" |

## CACHING LAYERS (implement in order)
1. **Scan-result cache** (Redis, key `scan:{chain}:{addr}`, TTL 10min regular /
   30min deep): identical re-scans return cached verdict instantly; `?fresh=1`
   bypasses. Store full card JSON so formatting is also cached.
2. **Per-section caches**: holder snapshots 5min, deployer profiles 24h (they
   rarely change), LP status 2min, price 30s. Sections refresh independently —
   a slow section never blocks fresh ones.
3. **Negative caching**: failed source lookups cached 5min to stop retry storms.
4. **Indexer-first**: ClickHouse indexer is TIER-0 for holders/deployer/
   transfers — RPC chain only when indexer lacks the chain/token.

## DATA COMPLETENESS MATRIX (every scan must attempt ALL sections)
| Section | Primary (indexer) | Fallback 1 | Fallback 2 | Honest-missing |
|---|---|---|---|---|
| Deployer profile | ClickHouse deployer_profiles | RPC getDeployer tx-scan | GMGN deployer | "unavailable" |
| Holder count/dist | ClickHouse holder_analysis | Ankr Advanced sampler | RPC paginated getLogs | "unavailable" |
| Bundle/sniper clusters | ClickHouse bundle_detect | GMGN bundle API | heuristic (same-block funders) | "unavailable" |
| LP status | indexer LP events | RPC LP pair read | DexScreener | "unavailable" |
| Honeypot sim | local honeypot engine | GoPlus | — | flag untested |
| Price/liquidity | indexer | DexScreener | CoinGecko | omit card price line |

## FALLBACK RULES
- Every section fetch wrapped: timeout → next source → honest-missing marker.
- A section failing NEVER fails the whole scan (fail-open per-section).
- rpc_router health feeds source ordering automatically (dead = deprioritized).
- All fallback decisions logged (`scan_section_fallback section= from= to=`)
  so completeness is auditable.

## OUTPUT QUALITY BAR (both regular and deep)
- Full analysis sections rendered (see formatter deep-analysis block).
- Risk score + top flags ALWAYS present.
- Links: dexscreener + rugmunch.io/scanner deep link.
- Progress streaming on deepscan (existing progress module wired to each
  section completion).

## EXECUTION ORDER
1. Redis scan-result cache + section caches (biggest speed win)
2. Indexer-first wiring for holders/deployer/bundles
3. Per-section fallback wrappers with logging
4. Deepscan beta-free flag + progress streaming polish
