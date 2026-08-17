"""Channel content fetchers — real backend/on-chain sources, fail-open.

Every fetch returns a normalized list of dicts (or ``[]``) and never raises.
Sources are the bot's own backend endpoints (``data_resolver.backend_get_json``
with the shared-secret header) plus the ClickHouse fetchers in
``channels.clickhouse_fetchers`` (own-indexed on-chain data).

Sources per channel
-------------------
* news   → ``GET /api/v1/news/trending?window_hours=168&limit=N``
* alpha  → whale moves: ``wallet_memory.token_transfers`` ≥ $10k via
           ``clickhouse_fetchers.fetch_whale_moves`` (own indexer, entity-
           labeled — replaces the never-populated WhaleWatcher alert history);
           early whale entries: first-block buyers of fresh tokens from
           ``deployer_events`` via ``clickhouse_fetchers.fetch_early_whale_entries``;
           smart money: ``smart_money_feed`` (live ``smart_money_wallets``
           source — the old ``/api/v1/databus/premium/smart-money`` endpoint
           is dead); launches: ``GET /api/v1/launches/feed``.
* alerts → ``GET /api/v1/launches/alerts`` (high-risk launch alerts with
           honeypot/rug/liquidity risk flags), MERGED with the webhook-trigger
           events already pushed to the Redis ``rmi:alerts:recent`` pipeline
           (``databus.alert_router.route_webhook_alert`` → ``push_alert``:
           deployer_dump / mint_freeze / lp_change / whale_move / transfer).
           Both rails flow through the same reranker gate downstream.

Nothing here is synthesized: every value comes from the listed live source.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from app.domains.telegram.rugmunchbot.data_resolver import backend_get_json
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_json

logger = get_logger("rmi_bot.channels.content")

_NEWS_WINDOW_HOURS = 168

#: Scanner gate the backend applies before an item appears on
#: ``/api/v1/launches/alerts`` (documented: tokens auto-scanned above 70/100
#: risk). Used only to render the honest "condition met vs current" line.
ALERT_RISK_THRESHOLD: int = 70

#: Hard budget for the concurrent DexScreener identity backfill in
#: ``fetch_scan_notes`` (fail-open — a timeout leaves names unresolved).
_IDENTITY_ENRICH_TIMEOUT: float = 6.0

#: Webhook-trigger event types (from ``databus.alert_router`` classification)
#: that should ALSO surface on the alerts channel digest. These arrive via
#: ``push_alert`` → Redis ``rmi:alerts:recent`` (``alert_type`` ends with
#: ``_webhook``) and were already gated at push time (``route_news`` skip
#: below the news band), so merging them here is additive, never spammy.
WEBHOOK_EVENT_TYPES: frozenset[str] = frozenset(
    {"deployer_dump", "mint_freeze", "lp_change", "whale_move", "token_transfer", "transfer"}
)

#: Webhook event type → the existing scanner-flag needle whose one-line
#: "why it matters" verdict (``formatters.alert_verdict``) honestly fits the
#: event. Events without a faithful needle keep the severity-based fallback.
WEBHOOK_VERDICT_FLAGS: dict[str, str] = {
    "deployer_dump": "high_risk_deployer",
    "mint_freeze": "mintable",
    "lp_change": "liquidity_removed",
}


def _image_url(value: Any) -> str:
    """Pass through a usable http(s) image URL from a backend payload ("" else)."""
    if isinstance(value, str) and value.strip().startswith(("http://", "https://")):
        return value.strip()
    return ""


def _alert_condition(item: dict[str, Any]) -> str:
    """One-line "condition met vs current" for a scanner alert item.

    When the payload carries a numeric ``risk_score`` the line states the
    current score against the documented alert threshold; otherwise it falls
    back to the risk-level verdict. Pure string building — never raises.
    """
    risk_level = str(item.get("risk_level") or "unknown")
    score = item.get("risk_score")
    if score is not None:
        with suppress(TypeError, ValueError):
            return f"risk {float(score):.0f}/100 ≥ {ALERT_RISK_THRESHOLD} ({risk_level})"
    return f"flagged {risk_level}"


def _scan_score_line(item: dict[str, Any]) -> str:
    """One-line score line for a general scan note (no alert-threshold claim).

    The launch-alert rail states "risk N/100 ≥ 70"; a general scan-history
    record only states its own score/level — we never claim a threshold that
    was not applied to it. Pure string building — never raises.
    """
    risk_level = str(item.get("risk_level") or "unknown")
    score = item.get("risk_score")
    if score is not None:
        with suppress(TypeError, ValueError):
            return f"risk {float(score):.0f}/100 ({risk_level})"
    return f"flagged {risk_level}"


def _is_placeholder(value: object) -> bool:
    """True when a name/symbol field carries no real identity (fail-open)."""
    return str(value or "").strip().lower() in ("", "unknown", "???", "n/a", "null", "none")


async def _enrich_scan_identity(item: dict[str, Any]) -> None:
    """Backfill a scan note's missing name/symbol from DexScreener (in place).

    Only fires when the note carries a placeholder identity (``Unknown`` /
    ``???`` / empty) — real payload names are never overwritten. Uses the
    free DexScreener ``/latest/dex/tokens/{address}`` endpoint via the shared
    budget-gated client (``http_get_json``). Fail-open: a miss, budget skip,
    or error leaves the note untouched; never raises.
    """
    if not (_is_placeholder(item.get("name")) or _is_placeholder(item.get("symbol"))):
        return
    addr = str(item.get("address") or item.get("token_address") or "").strip()
    if not addr:
        return
    try:
        body = await http_get_json(f"https://api.dexscreener.com/latest/dex/tokens/{addr}", timeout=5.0)
        if not body:
            return
        pairs = body.get("pairs")
        if not isinstance(pairs, list) or not pairs or not isinstance(pairs[0], dict):
            return
        base = pairs[0].get("baseToken")
        if not isinstance(base, dict):
            return
        if _is_placeholder(item.get("name")):
            candidate = str(base.get("name") or "").strip()
            if candidate:
                item["name"] = candidate
        if _is_placeholder(item.get("symbol")):
            candidate = str(base.get("symbol") or "").strip()
            if candidate:
                item["symbol"] = candidate
        if _is_placeholder(item.get("chain")):
            chain = str(pairs[0].get("chainId") or "").strip()
            if chain:
                item["chain"] = chain
    except Exception:
        logger.debug("channels:dex_identity_failed addr=%.40s", addr)


async def _enrich_missing_identities(items: list[dict[str, Any]]) -> None:
    """Concurrent, time-bounded DexScreener identity backfill for scan notes.

    Runs every placeholder-named note's lookup in parallel under a hard
    ``_IDENTITY_ENRICH_TIMEOUT`` so a slow DexScreener never blocks the
    channel tick. Fail-open by contract: any timeout/error leaves the list
    as-is.
    """
    if not items:
        return
    try:
        await asyncio.wait_for(
            asyncio.gather(*(_enrich_scan_identity(it) for it in items), return_exceptions=True),
            timeout=_IDENTITY_ENRICH_TIMEOUT,
        )
    except Exception:
        logger.debug("channels:dex_identity_timeout items=%d", len(items))


def _as_list(value: Any) -> list[dict[str, Any]]:
    """Coerce a JSON value into a list of dicts (fail-open)."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


# ── News ──────────────────────────────────────────────────────────────────


async def fetch_trending_news(limit: int = 5) -> list[dict[str, Any]]:
    """Curated trending headlines from our backend news feed."""
    body = await backend_get_json(
        "/api/v1/news/trending",
        params={"window_hours": _NEWS_WINDOW_HOURS, "limit": max(1, min(limit, 20))},
        timeout_secs=8.0,
    )
    if not body:
        return []
    items = _as_list(body.get("items") or body.get("articles"))
    return [
        {
            "title": str(item.get("title") or "").strip(),
            "summary": str(item.get("summary") or "").strip(),
            "url": str(item.get("url") or "").strip(),
            "source": str(item.get("source") or "unknown").strip(),
            "sentiment_label": str(item.get("sentiment_label") or "").strip(),
            "factual_rating": str(item.get("factual_rating") or "").strip(),
        }
        for item in items[:limit]
        if str(item.get("title") or "").strip()
    ]


# ── Alpha: whale moves + smart money + launches ───────────────────────────


async def _fetch_whale_moves(chain: str, limit: int) -> list[dict[str, Any]]:
    """Hourly top whale moves ≥ $10k from our own ClickHouse ``token_transfers``.

    Real on-chain data (value_decimal USD), entity-labeled via the entity
    registry — replaces the helius WhaleWatcher path whose ``_alert_history``
    was never populated. Fail-open ``[]`` when ClickHouse is down or quiet.
    """
    from app.domains.telegram.rugmunchbot.channels import clickhouse_fetchers

    return await clickhouse_fetchers.fetch_whale_moves(chain=chain, limit=limit)


async def _fetch_early_entries(chain: str, limit: int) -> list[dict[str, Any]]:
    """Early whale entries: wallets that entered fresh tokens in the first blocks.

    From ``deployer_events`` + the earliest ``token_transfers`` after each
    deploy; each entry carries the USD size. Fail-open ``[]``.
    """
    from app.domains.telegram.rugmunchbot.channels import clickhouse_fetchers

    return await clickhouse_fetchers.fetch_early_whale_entries(chain=chain, limit=limit)


async def _fetch_smart_money(chain: str, limit: int) -> list[dict[str, Any]]:
    """Smart-money wallets from the live ``smart_money_feed`` source (real).

    The previous backend premium endpoint (``/api/v1/databus/premium/smart-money``)
    is dead; the live feed still resolves ``smart_money_wallets``.
    """
    from app.domains.telegram.rugmunchbot.channels import clickhouse_fetchers

    return await clickhouse_fetchers.fetch_smart_money_wallets(chain=chain, limit=limit)


async def _fetch_launches(chain: str, limit: int) -> list[dict[str, Any]]:
    """Recent token launches from the backend launch feed (real)."""
    body = await backend_get_json(
        "/api/v1/launches/feed",
        params={"chain": chain, "limit": max(1, min(limit, 50))},
        timeout_secs=8.0,
    )
    if not body:
        return []
    launches = _as_list(body.get("launches"))
    return [
        {
            "address": str(item.get("address") or "").strip(),
            "name": str(item.get("name") or "Unknown").strip(),
            "symbol": str(item.get("symbol") or "???").strip(),
            "chain": str(item.get("chain") or chain).strip(),
            "risk_level": str(item.get("risk_level") or "unknown").strip(),
            "risk_flags": [str(f) for f in (item.get("risk_flags") or [])],
            "age_hours": item.get("age_hours"),
            "liquidity_usd": item.get("liquidity_usd") or item.get("liquidity"),
            "holders": item.get("holders"),
            "image_url": _image_url(item.get("image_url") or item.get("image")),
        }
        for item in launches[:limit]
        if str(item.get("address") or "").strip()
    ]


async def fetch_alpha_content(
    *,
    chain: str = "solana",
    whale_limit: int = 3,
    smart_limit: int = 5,
    launch_limit: int = 5,
    early_entry_limit: int = 2,
) -> dict[str, list[dict[str, Any]]]:
    """Assemble the alpha-channel content: whale moves + smart money + launches.

    Whale moves come from our own ClickHouse ``token_transfers`` (≥ $10k,
    entity-labeled); early whale entries (first-block buyers of fresh tokens,
    with size) are appended to the same list and tagged ``kind="early_entry"``
    so the scheduler's existing whale rail surfaces them untouched. Smart
    money comes from the live ``smart_money_feed``; launches from the backend
    launch feed.

    Returns ``{"whale_moves": [...], "smart_money": [...], "launches": [...]}``
    — each a list of normalized dicts, empty when the source has no data.
    Never raises.
    """
    whale_moves = await _fetch_whale_moves(chain, whale_limit)
    early_entries = await _fetch_early_entries(chain, early_entry_limit)
    whale_moves = [*early_entries, *whale_moves]
    smart_money = await _fetch_smart_money(chain, smart_limit)
    launches = await _fetch_launches(chain, launch_limit)
    return {"whale_moves": whale_moves, "smart_money": smart_money, "launches": launches}


# ── Alerts: scanner risk digest + webhook-trigger events ────────────────


def _webhook_event_label(event_type: str) -> str:
    """Readable event-type label for a webhook alert ("deployer_dump" → "deployer dump")."""
    return (event_type or "webhook").replace("_", " ").strip() or "webhook"


def _webhook_item(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one pushed webhook alert (Redis ``rmi:alerts:recent``) into
    the alerts-channel shape, with entity labels + a one-line why-it-matters.

    Input is one entry as returned by ``app.alert_pipeline.get_recent_alerts``
    (the ``push_alert`` payload: ``type`` = ``<service>_webhook``, plus
    ``event_type`` / ``attribution`` / ``importance`` metadata spread at the
    top level). Returns ``None`` for hostile rows (non-matching alert types).
    Pure string building — never raises.
    """
    alert_type = str(entry.get("type") or entry.get("alert_type") or "").strip()
    event_type = str(entry.get("event_type") or "").strip()
    if not (alert_type.endswith("_webhook") or event_type in WEBHOOK_EVENT_TYPES):
        return None
    label = event_type or _webhook_event_label(alert_type.removesuffix("_webhook"))
    readable = _webhook_event_label(label)

    token = str(entry.get("token") or "").strip()
    symbol = str(entry.get("token_symbol") or "").strip()
    chain = str(entry.get("chain") or "solana").strip()
    severity = str(entry.get("severity") or "medium").strip().lower()
    title = str(entry.get("title") or "").strip()
    description = str(entry.get("description") or "").strip()
    attribution_raw = entry.get("attribution")
    attribution = attribution_raw if isinstance(attribution_raw, dict) else {}
    from_label = str(attribution.get("from") or "").strip()
    to_label = str(attribution.get("to") or "").strip()

    side = " → ".join(x for x in (from_label, to_label) if x)
    condition = f"webhook · {readable}" + (f" — {side}" if side else "")

    flags = [label]
    verdict_flag = WEBHOOK_VERDICT_FLAGS.get(label)
    if verdict_flag and verdict_flag not in flags:
        flags.append(verdict_flag)

    name = symbol or from_label or readable.title()
    return {
        "address": token,
        "token_address": token,
        "name": name,
        "symbol": symbol or readable,
        "chain": chain,
        "risk_level": severity,
        "risk_score": entry.get("risk_score"),
        "risk_flags": [str(f) for f in flags][:6],
        "title": title or f"{readable.title()} alert",
        "summary": description,
        "verdict": description,
        "condition": condition,
        # Human-readable type (formatter renders ``alert_type.title()``, so
        # snake_case would leak underscores: "deployer_dump" → "Deployer_Dump").
        "alert_type": readable,
        "url": str(entry.get("id") or "").strip(),
        "wallet_short": from_label or to_label,
        "created_at": str(entry.get("created_at") or "").strip(),
        "image_url": "",
    }


async def _fetch_pushed_webhook_alerts(limit: int) -> list[dict[str, Any]]:
    """Recent webhook-trigger alerts from the Redis ``rmi:alerts:recent`` pipe.

    Reads the same site feed ``route_webhook_alert`` writes via ``push_alert``
    (canonical reader: ``app.alert_pipeline.get_recent_alerts``). Only entries
    whose ``type`` ends with ``_webhook`` and whose ``event_type`` is one of
    :data:`WEBHOOK_EVENT_TYPES` are kept (unknown/dust rows are skipped); a
    ``_webhook`` entry without an ``event_type`` is kept when its severity is
    high/critical. Fail-open: Redis down → ``[]``, the digest still runs.
    """
    try:
        from app.alert_pipeline import get_recent_alerts

        entries = await get_recent_alerts(limit=limit * 2, severity="")
    except Exception as exc:  # fail-open by contract
        logger.debug("channels:webhook_alerts_unavailable err=%s", str(exc)[:120])
        return []
    items: list[dict[str, Any]] = []
    for entry in entries:
        alert_type = str(entry.get("type") or entry.get("alert_type") or "").strip()
        event_type = str(entry.get("event_type") or "").strip()
        if not alert_type.endswith("_webhook"):
            continue
        if event_type not in WEBHOOK_EVENT_TYPES and (
            event_type or str(entry.get("severity") or "").lower() not in ("high", "critical")
        ):
            continue
        item = _webhook_item(entry)
        if item is not None:
            items.append(item)
        if len(items) >= limit:
            break
    return items


async def fetch_risk_alerts(limit: int = 5) -> list[dict[str, Any]]:
    """Recent high-risk scanner alerts MERGED with webhook-trigger events.

    Two rails, one digest:

    * Scanner rail — ``/api/v1/launches/alerts`` (tokens auto-scanned above
      70/100 risk with their honeypot/rug/liquidity flags).
    * Webhook rail — ``rmi:alerts:recent`` in Redis: deployer dumps, mint /
      freeze toggles, LP changes, whale moves and large transfers that the
      databus alert router classified and pushed. These currently reach user
      DMs / the site feed but NOT the channel — merging them here lets the
      channel's existing reranker gate decide (below the floor → dropped).

    Items are interleaved scanner-first so a live webhook event still appears
    when the scanner rail is quiet (and vice-versa). Never raises.
    """
    max_items = max(1, min(limit, 20))
    body = await backend_get_json(
        "/api/v1/launches/alerts",
        params={"limit": max_items},
        timeout_secs=8.0,
    )
    scanner: list[dict[str, Any]] = []
    if body:
        alerts = _as_list(body.get("alerts"))
        normalized: list[dict[str, Any]] = []
        for item in alerts[:max_items]:
            security = item.get("security")
            raw_flags = item.get("risk_flags")
            flags = [str(f) for f in raw_flags] if isinstance(raw_flags, list) else []
            if isinstance(security, dict):
                for k, v in security.items():
                    if v is True and k not in {str(f).lower() for f in flags}:
                        flags.append(k)
            normalized.append(
                {
                    "address": str(item.get("address") or "").strip(),
                    "name": str(item.get("name") or "Unknown").strip(),
                    "symbol": str(item.get("symbol") or "???").strip(),
                    "chain": str(item.get("chain") or "").strip(),
                    "risk_level": str(item.get("risk_level") or "unknown").strip(),
                    "risk_score": item.get("risk_score"),
                    "risk_flags": [str(f) for f in flags][:6],
                    "title": str(item.get("title") or "").strip(),
                    "alert_type": str(item.get("alert_type") or "scanner").strip(),
                    "condition": _alert_condition(item),
                    "image_url": _image_url(item.get("image_url") or item.get("image")),
                    # Rich scan fields passthrough (best-effort) so card
                    # formatters can surface the REAL why (security booleans,
                    # deployer, liquidity) instead of a generic verdict.
                    "security": security if isinstance(security, dict) else None,
                    "deployer": str(
                        item.get("deployer") or item.get("creator_address") or item.get("creator") or ""
                    ).strip()
                    or None,
                    "liquidity": item.get("liquidity"),
                    "age_hours": item.get("age_hours"),
                    "holders": item.get("holders"),
                }
            )
        scanner = [a for a in normalized if a["address"]]

    webhook = await _fetch_pushed_webhook_alerts(max_items)

    # Interleave scanner-first (preserves today's behavior when the webhook
    # rail is empty) so both sources are represented within the digest window.
    merged: list[dict[str, Any]] = []
    for i in range(max_items):
        if i < len(scanner):
            merged.append(scanner[i])
        if i < len(webhook):
            merged.append(webhook[i])
    return merged[:limit]


# ── Scans channel: "scans of note" pool ──────────────────────────────────


def _scan_history_to_note(item: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one scan-history record (ScanRecord shape) into a scan note.

    The backend ``/api/v1/data/scan-history`` returns the frontend ScanRecord
    contract (``contract_address``/``token_name``/``token_symbol``/…); this
    maps it onto the same normalized dict the launch-alert rail produces
    (``address``/``name``/``symbol``/``risk_flags``/``condition``) so the two
    rails can merge. Derives flags from the boolean ScanRecord fields when no
    raw ``risk_flags`` list is present. Returns ``None`` for records without
    an address (fail-open).
    """
    address = str(item.get("contract_address") or item.get("token_address") or "").strip()
    if not address:
        return None
    flags = [str(f).lower() for f in (item.get("risk_flags") or [])]
    if not flags:
        if item.get("is_honeypot"):
            flags.append("honeypot")
        if item.get("is_rug_pull"):
            flags.append("rug_pull")
        if item.get("has_mint_authority"):
            flags.append("mint_authority")
        if item.get("liquidity_locked") is False:
            flags.append("liquidity_removed")
    name = str(item.get("token_name") or "Unknown").strip()
    return {
        "address": address,
        "name": name,
        "symbol": str(item.get("token_symbol") or "???").strip(),
        "chain": str(item.get("chain") or "").strip(),
        "risk_level": str(item.get("risk_level") or "unknown").strip(),
        "risk_score": item.get("risk_score"),
        "risk_flags": [str(f) for f in flags][:6],
        "scanned_at": str(item.get("scanned_at") or "").strip(),
        "title": name,
        "alert_type": "scan",
        "condition": _scan_score_line(item),
    }


async def fetch_scan_notes(limit: int = 10) -> list[dict[str, Any]]:
    """The day's most notable scanned tokens (launch-alert rail + scan history).

    Merges the high-risk launch-alert rail (``/api/v1/launches/alerts``,
    tokens auto-scanned above 70/100 — the same rail ``fetch_risk_alerts``
    uses) with recent scan history (``/api/v1/data/scan-history`` — Postgres
    ``scan_results`` with Redis-cache fallback, newest first). Items are
    deduped by token address (the launch-alert variant wins on ties because
    it carries the richer risk flags + alert condition), then sorted by risk
    score descending so the most notable scans surface first. Returns ``[]``
    when neither source has data — the caller fails open and posts nothing.
    Never raises.
    """
    alerts = await fetch_risk_alerts(max(limit * 2, 10))
    body = await backend_get_json(
        "/api/v1/data/scan-history",
        params={"limit": max(1, min(limit * 2, 100))},
        timeout_secs=8.0,
    )
    merged: dict[str, dict[str, Any]] = {}
    for item in alerts:
        addr = str(item.get("address") or "").strip().lower()
        if addr:
            merged[addr] = item
    for raw in _as_list(body.get("scans")) if body else []:
        note = _scan_history_to_note(raw)
        if note is None:
            continue
        addr = str(note.get("address") or "").strip().lower()
        if not addr:
            continue
        existing = merged.get(addr)
        if existing is None:
            merged[addr] = note
            continue
        # Launch-alert variant already present — backfill the fields it
        # lacks (scan timestamp) from the scan-history record.
        if not existing.get("scanned_at"):
            existing["scanned_at"] = note.get("scanned_at") or ""
    notes = list(merged.values())

    def _score_key(item: dict[str, Any]) -> float:
        value = item.get("risk_score")
        if isinstance(value, (int, float)):
            return float(value)
        return -1.0

    notes.sort(key=_score_key, reverse=True)
    notes = notes[: max(1, limit)]
    await _enrich_missing_identities(notes)
    return notes


__all__ = [
    "fetch_alpha_content",
    "fetch_risk_alerts",
    "fetch_scan_notes",
    "fetch_trending_news",
]
