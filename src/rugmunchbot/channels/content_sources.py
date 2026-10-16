"""Per-content-type SOURCE registry — the ONE declared source per content type.

Operator directive (2026-10-17, report 98 follow-up): the charter
(``content_charter.py``) says WHICH content types a channel may post; this
module says WHERE each content type's data comes from. Together they are the
complete content plan: charter = what/where-it-goes, sources = where-it-comes-from.

Why a registry and not prose: a content type with no declared source is a
content type nobody can feed — the exact class of defect that left
``@rmialpha`` and ``@rmiscans`` quiet. ``test_content_sources.py`` asserts every
canonical content type has a declared source, so a new type cannot ship
unwired.

Each :class:`ContentSource` names the ingestion rail (RugBrain collection /
scanner / indexer / backend endpoint), the enrichment step, the rerank lane,
and the render path — the four stages of the data flow, in one row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from rugmunchbot.channels.content_charter import CANONICAL_CONTENT_TYPES


@dataclass(frozen=True)
class ContentSource:
    """Where one content type's data comes from, end to end."""

    content_type: str
    #: The ingestion rail — RugBrain collection, scanner, indexer, or endpoint.
    ingest: str
    #: The enrichment step (entity labels, dedupe, severity, identity backfill).
    enrich: str
    #: The rerank / gate lane that decides what actually posts.
    rerank: str
    #: The render path (formatter + graphic).
    render: str


# ══════════════════════════════════════════════════════════════════════════
# THE SOURCE REGISTRY — one row per canonical content type
# ══════════════════════════════════════════════════════════════════════════
CONTENT_SOURCES: Final[dict[str, ContentSource]] = {
    "market": ContentSource(
        content_type="market",
        ingest=(
            "daily_market_summary: CoinGecko simple/price + global metrics + "
            "trending/movers; daily_rug: rugbrain news-rerank over news_v2"
        ),
        enrich=(
            "RAG intel lanes — news_v2 + threat_intel_v2 + whale_transfers + "
            "hacks_v2 + market_health + regulation_enforcement (HybridRetriever)"
        ),
        rerank="rugbrain consumer reranker POST /api/v1/rag/news-rerank (24h window)",
        render="daily_market_summary/_fmt + daily_rug longform; graphic market→rmi-evening-market-wrap.jpeg",
    ),
    "roundup": ContentSource(
        content_type="roundup",
        ingest="scheduler/_posts_roundup: the day's already-posted stories (seen-store)",
        enrich="dedupe against the day's posted set (no story repeats)",
        rerank="importance rerank (channels.importance) before selection",
        render="compose_roundup digest; graphic roundup→rug-munch-logo.jpg",
    ),
    "news": ContentSource(
        content_type="news",
        ingest=(
            "telegram_news sweep (Telethon) → news_v2; fallback rugbrain "
            "news_v2 rail + backend /api/v1/news/trending"
        ),
        enrich="quality filter → rate limiter → chunk → embed; multi-key canonical-URL dedupe",
        rerank="BAAI/bge-reranker-v2-m3 importance (channels.importance); floor 0.4, main gate 0.7",
        render="telegram_news/_digest + formatters; graphic news→rmi-breaking-news.jpeg",
    ),
    "alert": ContentSource(
        content_type="alert",
        ingest=(
            "scanner_alerts: rugbrain GET /api/v1/alerts/recent (scanner "
            "detections via Redis rmi:rag:ingest); exploit_alerts: rugbrain "
            "exploit_drafts.json; content.pipeline: /api/v1/launches/alerts + "
            "Redis rmi:alerts:recent webhook events"
        ),
        enrich="entity labels + severity + top-3 red flags + one source link",
        rerank="importance rerank; alerts floor 0.4, main escalation gate 0.7",
        render="formatters.format_alerts_post / format_main_escalation; graphic alert→current_cryptorugmuncher.png",
    ),
    "scan": ContentSource(
        content_type="scan",
        ingest=(
            "content.pipeline.fetch_scan_notes: /api/v1/launches/alerts "
            "(auto-scanned >70/100) merged with /api/v1/data/scan-history "
            "(Postgres scan_results)"
        ),
        enrich="_enrich_missing_identities (entity labels) + dedupe by token address",
        rerank="risk-score sort + reranker gate; _scan_content_type_gate blocks non-scan kinds",
        render="formatters.format_scan_card (verdict + risk + chain); graphic scan→current_cryptorugmuncher.png",
    ),
    "whale": ContentSource(
        content_type="whale",
        ingest=(
            "clickhouse_fetchers.fetch_whale_moves: wallet_memory.token_transfers "
            "≥ $10k; fetch_early_whale_entries: wallet_memory.deployer_events + "
            "first transfers"
        ),
        enrich="entity-labeled (wallet_memory.entity_labels) + size + chain",
        rerank="HARD quality gate (whale alpha is paid-product) before send",
        render="scheduler/_posts_whales.build_whale_post; graphic whale→rmi-whale-alert.jpeg",
    ),
    "alpha": ContentSource(
        content_type="alpha",
        ingest=(
            "content.pipeline.fetch_alpha_content: smart_money_feed "
            "(smart_money_wallets) + /api/v1/launches/feed + SearXNG kol_trades"
        ),
        enrich="entity labels + PnL + source link (cite-or-omit)",
        rerank="importance rerank; alpha category routes below the main gate",
        render="formatters.format_alpha_post; graphic alpha→current_cryptorugmuncher.png",
    ),
    "shill": ContentSource(
        content_type="shill",
        ingest=(
            "shill_campaign_post: rugbrain POST /rag/shill-campaigns (3+ KOLs, "
            "same token, 24h); shill_watch: KOL channel sweep + backend detect"
        ),
        enrich="X screenshot + OCR (VL-4B) → shill_evidence + threat_intel_v2; scanner evidence",
        rerank="importance rerank; main gate 0.7, alerts floor 0.4",
        render="shill_watch.format_shill_alert / build_shill_campaign_post; graphic shill→current_cryptorugmuncher.png",
    ),
    "scam_alert": ContentSource(
        content_type="scam_alert",
        ingest=(
            "scam_alerts/_publisher: /var/lib/rugbrain/shill_alerts.jsonl "
            "(rugbrain shill-alert pipeline)"
        ),
        enrich="severity from risk (0-5) + addresses/tickers/categories + verify link",
        rerank="risk sort + rugbrain rerank tie-break (collection scam_patterns)",
        render="formatters.format_scam_alert; graphic scam_alert→rmi-scam-alert.jpeg",
    ),
    "update": ContentSource(
        content_type="update",
        ingest="scheduler/_posts_updates: _probe_services() health probe + release notes",
        enrich="health-line assembly (service status)",
        rerank="channel_accepts_content('updates','update') contract gate",
        render="compose_updates_digest; graphic update→rug-munch-media-llc.png",
    ),
    "system": ContentSource(
        content_type="system",
        ingest="ops/health probes (service status, non-security)",
        enrich="status assembly",
        rerank="none — informational, never gated by importance",
        render="system notification; graphic system→rug-munch-media-llc.png",
    ),
}


def source_for(content_type: str) -> ContentSource | None:
    """The declared source row for *content_type* (None when undeclared)."""
    if not content_type:
        return None
    return CONTENT_SOURCES.get(str(content_type).strip().lower())


def undeclared_content_types() -> frozenset[str]:
    """Canonical content types with NO declared source — must be empty."""
    return CANONICAL_CONTENT_TYPES - frozenset(CONTENT_SOURCES)


__all__ = [
    "CONTENT_SOURCES",
    "ContentSource",
    "source_for",
    "undeclared_content_types",
]
