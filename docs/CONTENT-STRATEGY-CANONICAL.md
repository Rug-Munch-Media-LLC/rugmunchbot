# CONTENT STRATEGY — CANONICAL v2 (2026-08-25)

> Owner directive, canonical. Supersedes earlier strategy notes.
> Every channel has a CONTRACT. The bot is an ADMIN in main — it curates,
> it does not spam. Quality over volume, every post carries substance.

## CHANNEL MATRIX & CONTRACTS

### @cryptorugmuncher (MAIN) — TOP TIER ACROSS THE BOARD
- **Who posts**: bot (as admin) — curated only
- **What**: top-tier content ONLY:
  - Scans of interest → **FULL scan outputs** (complete verdict cards with
    all analysis sections), never truncated teasers
  - Exploits/hacks/confirmed rugs (always escalate, W18)
  - Major market events with commentary + links (W19/W20)
- **Quality bar**: fix the output BEFORE pushing more — a broken/incomplete
  scan card must never ship; formatter failures block publication
- **Cadence**: ≤9/day cap stands, but never filler — if nothing clears the
  bar, post nothing
- **Mirror**: EVERY main post → feed.rugmunch.io (headless Ghost "twitter
  clone" for operator social presence). Mirror is mandatory, not optional;
  mirror failure alerts (W30)

### @rmialerts (ALERTS) — REVISED DOCTRINE
- **What**: coins very likely to rug (rug_prediction ≥ threshold), active
  exploits/hacks, verified scams, LP bombs IN PROGRESS
- **Never**: price-movement-only items, "notable whale moved" without risk
  context, anything without a verdict + risk score
- **Format**: severity emoji + token + verdict + top-3 red flags + links
- **Rerank**: importance rerank before posting; multiple posts/day allowed
  when signal justifies

### @rmicryptonews (NEWS) — IMPROVED + RERANKED
- **What**: real news with bodies (source-quality gate live), deduped across
  lanes, importance-reranked
- **Cadence**: MULTIPLE posts/day (digest rotation already varies format)
- **Every post**: link required, dated, sourced

### ALPHA / WHALES — DATA-DRIVEN
- **What**: whale convergence/funding dislocation/social spikes FROM OUR OWN
  DATA (ClickHouse signals), reranked, risk-labeled
- **Cadence**: multiple pushes/day when data warrants; never schedule-filler

### SCANS OUTPUT UPGRADE (critical gap)
Current scan cards MISS core analysis. Required fields on EVERY scan/deepscan:
1. **Deployer profile**: who deployed (address, past tokens launched,
   rug history from deployer_profiles ClickHouse)
2. **Holder count + distribution**: total holders, top-10 concentration,
   fresh-wallet % (own_holder_analysis data)
3. **Bundle/sniper clustering**: bundled supply %, cluster count, sniper
   entries (bundle_detect engines)
4. **LP status**: locked/burned/%, lock expiry
5. **Verdict + risk score** with the flags that drove it
If any section's data source fails → show "unavailable" honestly, never
omit silently.

### DEEPSCANS — FREE DURING BETA
- Deepscan = no x402/payment gate while in beta; users test + compare
- Add `BETA_FREE_DEEPSCAN=1` flag gating the payment branch
- Beta banner on deepscan output ("free during beta — feedback welcome")

## RAG INTEGRATION (now live)
- news sweep → ingest_into_rag → news_v2 @2560 ✓ (ingested=163 verified)
- MultiDimAnnIndex: per-dim spaces, dim_registry identity ✓
- Chat: force RAG consult on analysis intent + citations (W12/W13 next)
- scans/alerts → RAG docs (W7/W8 next)

## IMMEDIATE EXECUTION ORDER
1. Scanner card upgrade: wire deployer_profiles + holder_analysis +
   bundle clustering into format_scan_card (full outputs)
2. Deepscan beta-free flag
3. Main→feed mirror enforcement + alerting
4. W12/W13 chat citations
5. W7/W8 scans/alerts→RAG
6. Per-channel prompt templates (style guides)
