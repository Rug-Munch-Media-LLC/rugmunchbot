"""Channel content fetchers — real backend/on-chain sources, fail-open.

Every fetch returns a normalized list of dicts (or ``[]``) and never raises.
Sources are the bot's own backend endpoints (``data_resolver.backend_get_json``
with the shared-secret header) plus the ClickHouse fetchers in
``channels.clickhouse_fetchers`` (own-indexed on-chain data).

Split into sub-modules (2026-09-05):

* ``pipeline``   — content pipeline fetchers (news / alpha / alerts / scans / RAG)
* ``formatters`` — one-line formatting helpers (pure string building)
* ``quality``    — suspect-scan churn rail + honesty gates
* ``helpers``    — shared fail-open helpers (identity backfill, coercion)

This ``__init__`` re-exports the full public surface so existing
``from rugmunchbot.channels.content import X`` imports (and tests that
monkeypatch attributes on this package) keep working unchanged.
"""

from __future__ import annotations

from rugmunchbot.channels.content.formatters import (
    ALERT_RISK_THRESHOLD,
    _alert_condition,
    _scan_score_line,
    _webhook_event_label,
)
from rugmunchbot.channels.content.helpers import (
    _IDENTITY_ENRICH_TIMEOUT,
    _NEWS_WINDOW_HOURS,
    _as_list,
    _build_risk_report_from_scan,
    _enrich_missing_identities,
    _enrich_scan_identity,
    _float_or_none,
    _holders_count,
    _image_url,
    _is_placeholder,
    logger,
)
from rugmunchbot.channels.content.pipeline import (
    ALERT_EVENT_TYPES,
    WEBHOOK_EVENT_TYPES,
    WEBHOOK_VERDICT_FLAGS,
    WHALE_EVENT_TYPES,
    _fetch_early_entries,
    _fetch_kol_trades,
    _fetch_kol_trades_apipass,
    _fetch_kol_trades_searxng,
    _fetch_launches,
    _fetch_pushed_webhook_alerts,
    _fetch_smart_money,
    _fetch_whale_moves,
    _scan_history_to_note,
    _webhook_item,
    fetch_alpha_content,
    fetch_rag_drafts,
    fetch_risk_alerts,
    fetch_scan_notes,
    fetch_trending_news,
)
from rugmunchbot.channels.content.quality import (
    _SCAN_CHURN_CONCURRENCY,
    _SCAN_CHURN_MIN_SIGNAL,
    _SCAN_CHURN_POLL_MAX_S,
    _SCAN_CHURN_SCAN_TIMEOUT,
    _churn_one_scan,
    _scan_signal_count,
    _score_risk_level,
    fetch_suspect_scan_notes,
)

__all__ = [
    "ALERT_EVENT_TYPES",
    "ALERT_RISK_THRESHOLD",
    "WEBHOOK_EVENT_TYPES",
    "WEBHOOK_VERDICT_FLAGS",
    "WHALE_EVENT_TYPES",
    # Private names re-exported for backward compatibility (tests monkeypatch
    # and read some of these on the package; schedulers import others).
    "_IDENTITY_ENRICH_TIMEOUT",
    "_NEWS_WINDOW_HOURS",
    "_SCAN_CHURN_CONCURRENCY",
    "_SCAN_CHURN_MIN_SIGNAL",
    "_SCAN_CHURN_POLL_MAX_S",
    "_SCAN_CHURN_SCAN_TIMEOUT",
    "_alert_condition",
    "_as_list",
    "_build_risk_report_from_scan",
    "_churn_one_scan",
    "_enrich_missing_identities",
    "_enrich_scan_identity",
    "_fetch_early_entries",
    "_fetch_kol_trades",
    "_fetch_kol_trades_apipass",
    "_fetch_kol_trades_searxng",
    "_fetch_launches",
    "_fetch_pushed_webhook_alerts",
    "_fetch_smart_money",
    "_fetch_whale_moves",
    "_float_or_none",
    "_holders_count",
    "_image_url",
    "_is_placeholder",
    "_scan_history_to_note",
    "_scan_score_line",
    "_scan_signal_count",
    "_score_risk_level",
    "_webhook_event_label",
    "_webhook_item",
    "fetch_alpha_content",
    "fetch_rag_drafts",
    "fetch_risk_alerts",
    "fetch_scan_notes",
    "fetch_suspect_scan_notes",
    "fetch_trending_news",
    "logger",
]
