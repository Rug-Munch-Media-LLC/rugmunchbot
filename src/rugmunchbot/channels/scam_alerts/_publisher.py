"""Scam alerts publisher for RugBrain shill alerts.

Consumes /var/lib/rugbrain/shill_alerts.jsonl and publishes new alerts to
Telegram main channel and Ghost feed page using existing publishing helpers.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from rugmunchbot.channels.scheduler._posts_rag._feed import (
    mirror_post_to_feed,
)
from rugmunchbot.channels.telegram_news._digest import (
    publish_story_to_feed,
)
from rugmunchbot.channels.telegram_news._ledger import (
    evaluate_report,
    is_published,
    mark_published,
)
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.scam_alerts")

# Configuration
SHILL_ALERTS_FILE = "/var/lib/rugbrain/shill_alerts.jsonl"
CURSOR_FILE = os.getenv("RMI_SCAM_ALERTS_CURSOR", "/var/lib/rugmunchbot/scam_alerts.cursor")
MIN_WORDS = 50  # Minimum words for a valid alert post
POST_TO_TELEGRAM = os.getenv("RMI_SCAM_ALERTS_POST_TELEGRAM", "true").lower() in (
    "1",
    "true",
    "yes",
)
POST_TO_FEED = os.getenv("RMI_SCAM_ALERTS_POST_FEED", "true").lower() in ("1", "true", "yes")


def _read_cursor() -> int:
    """Read the byte cursor from the cursor file."""
    try:
        with open(CURSOR_FILE) as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _write_cursor(position: int) -> None:
    """Write the byte cursor to the cursor file."""
    with open(CURSOR_FILE, "w") as f:
        f.write(str(position))


def _tail_shill_alerts() -> list[dict[str, Any]]:
    """Tail new shill alerts from the JSONL file."""
    if not Path(SHILL_ALERTS_FILE).exists():
        logger.debug("scam_alerts:shill_alerts_file_missing")
        return []

    cursor = _read_cursor()
    file_size = Path(SHILL_ALERTS_FILE).stat().st_size

    # If file is smaller than cursor, reset cursor (file was rotated/truncated)
    if file_size < cursor:
        cursor = 0

    new_alerts = []
    new_cursor = cursor

    try:
        with open(SHILL_ALERTS_FILE, encoding="utf-8") as f:
            f.seek(cursor)
            # ``for line in f`` + ``f.tell()`` raises "telling position disabled
            # by next() call" (CPython kills tell() once the text iterator
            # buffers ahead), which stalled this lane on ``file_read_error``.
            # ``readline()`` keeps tell() valid; a line without its trailing
            # newline is a partial append, so break and re-read it next tick.
            while True:
                line = f.readline()
                if not line or not line.endswith("\n"):
                    break
                new_cursor = f.tell()
                line = line.strip()
                if not line:
                    continue

                try:
                    alert = json.loads(line)
                    new_alerts.append(alert)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "scam_alerts:json_decode_error line=%s err=%s",
                        line[:100],
                        str(exc),
                    )
    except Exception as exc:
        logger.error("scam_alerts:file_read_error err=%s", str(exc))
        return []

    # Update cursor only if we successfully read new data
    if new_cursor > cursor:
        _write_cursor(new_cursor)

    return new_alerts


#: risk at or above this also hits the MAIN channel + feed (operator 2026-10-12:
#: every shill goes to the alerts page; the big ones also go to main).
MAIN_RISK = int(os.getenv("RMI_SCAM_ALERTS_MAIN_RISK", "5"))

#: how a finding ranks against the others in one cycle
SEVERITY_QUERY = (
    "large scale crypto rug pull scam, mass shill campaign, guaranteed returns, "
    "drainer, honeypot contract, users losing money"
)


def severity(alert: dict[str, Any]) -> int:
    """Numeric severity: the detector risk, never below zero."""
    try:
        return max(0, int((alert.get("metadata") or {}).get("risk", 0)))
    except (TypeError, ValueError):
        return 0


def destinations_for(alert: dict[str, Any]) -> tuple[str, ...]:
    """Which destinations this finding belongs in.

    alerts page + alerts channel for everything; MAIN channel + main feed only
    for the big ones (risk >= MAIN_RISK).
    """
    return ("alerts", "main") if severity(alert) >= MAIN_RISK else ("alerts",)


async def rank_alerts(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order findings worst-first: risk, then the hosted 8B rerank boost.

    Rerank is a TIE-BREAKER only (it reorders equal-risk items by how much the
    text reads like a real scam campaign). Any failure leaves the risk order
    untouched — a ranking aid must never lose alerts.
    """
    if len(alerts) < 2:
        return alerts
    ordered = sorted(alerts, key=severity, reverse=True)
    try:
        from rugmunchbot._vendor import rugbrain_client

        docs = [f"{a.get('text', '')[:600]}" for a in ordered]
        scored = await rugbrain_client.rugbrain_search(
            query=SEVERITY_QUERY,
            documents=docs,
            rerank=True,
            collection="scam_patterns",
        )
        boosts: dict[str, float] = {}
        for row in scored or []:
            idx = row.get("index")
            if isinstance(idx, int) and 0 <= idx < len(ordered):
                boosts[str(ordered[idx].get("id"))] = float(
                    row.get("rerank_score") or row.get("score") or 0.0
                )
        if boosts:
            ordered.sort(
                key=lambda a: (severity(a), boosts.get(str(a.get("id")), 0.0)),
                reverse=True,
            )
    except Exception as exc:
        logger.debug("scam_alerts:rerank_unavailable err=%s", str(exc)[:100])
    return ordered


def _severity_from_risk(risk: int) -> str:
    """Map a RugBrain 0-5 risk to the scam-alert severity vocabulary."""
    if risk >= 5:
        return "critical"
    if risk >= 4:
        return "high"
    if risk >= 3:
        return "medium"
    return "low"


def _render_alert_post(alert: dict[str, Any]) -> str:
    """Render a shill alert into the DEDICATED scam-alert card.

    Operator directive (2026-10-17): scam alerts are their own post type —
    severity framing + concrete intel + verify link — not a generic
    "Shill Alert: <headline>" dump. Delegates to
    ``formatters.format_scam_alert`` so the copy + graphic are owned in one
    place. Falls back to a minimal honest card when the formatter is
    unavailable (fail-open — an alert must never be lost to a format bug).
    """
    metadata = alert.get("metadata") or {}
    risk = severity(alert)
    addresses = [str(a) for a in (metadata.get("addresses") or []) if str(a).strip()]
    tickers = [str(t) for t in (metadata.get("tickers") or []) if str(t).strip()]
    text = str(alert.get("text") or "").strip()
    item: dict[str, Any] = {
        "severity": _severity_from_risk(risk),
        "address": addresses[0] if addresses else "",
        "symbol": tickers[0] if tickers else "",
        "chain": str(metadata.get("chain") or ""),
        "scam_intel": text[:400],
        "source": str(metadata.get("channel") or ""),
        "risk_flags": [str(c) for c in (metadata.get("categories") or []) if str(c).strip()],
    }
    try:
        from rugmunchbot.channels.formatters import format_scam_alert

        return str(format_scam_alert(item))
    except Exception as exc:  # fail-open — never lose an alert to a format bug
        logger.warning("scam_alerts:format_fallback err=%s", str(exc)[:120])
        head = text.split("\n")[0][:200] if text else "RugBrain detection"
        return (
            f"🚨 <b>SCAM ALERT</b>\n\n{head}\n\n"
            f"<b>Risk:</b> {risk}/5 · <b>Channel:</b> {metadata.get('channel', 'unknown')}\n"
            "🔍 <a href='https://rugmunch.io/scanner'>Verify on RugMunch scanner</a>"
        )


async def _publish_alert_to_telegram(
    bot, chat_id: str, alert_post: str, alert_id: str = ""
) -> bool:
    """Publish an alert to Telegram channel."""
    if not POST_TO_TELEGRAM or not chat_id:
        return False

    try:
        from telegram.constants import ParseMode

        from rugmunchbot.channels.scheduler._core import _safe_send

        # Apply quality gate
        from rugmunchbot.support.quality_gate import pre_post_quality_gate

        gate = await pre_post_quality_gate(
            alert_post,
            source=f"scam_alert:{alert_id or 'unknown'}",
            kind="scam_alert",
            logger=logger,
        )

        if not gate.ok:
            logger.info("scam_alert_skipped reason=quality_gate_failed id=%s", alert_id)
            return False

        # Photo-aware send (2026-10-17): the scam-alert card carries its
        # dedicated brand graphic. Attach it via the shared photo primitive
        # (fail-open to text when the image send fails).
        from rugmunchbot.channels._media import send_news_photo
        from rugmunchbot.support.post_graphics import graphic_for

        graphic = graphic_for("scam_alert")
        if graphic:
            sent = await send_news_photo(bot, chat_id, caption=gate.text, photo_url=graphic)
        else:
            sent = await _safe_send(
                bot,
                chat_id,
                gate.text,
                parse_mode=ParseMode.HTML,
                content_type="scam_alert",
            )

        if sent:
            logger.info("scam_alert_posted_telegram id=%s", alert_id)
            return True
        else:
            logger.warning("scam_alert_send_failed id=%s", alert_id)
            return False

    except Exception as exc:
        logger.error("scam_alert_telegram_error err=%s", str(exc))
        return False


async def _publish_alert_to_feed(alert_post: str, alert_title: str, alert_id: str = "") -> bool:
    """Publish an alert to the Ghost feed page."""
    if not POST_TO_FEED:
        return False

    try:
        # Use the existing publish_story_to_feed helper
        result = await publish_story_to_feed(alert_post, title=alert_title or "Shill Alert")

        if result:
            logger.info("scam_alert_posted_feed id=%s", alert_id or "unknown")
            # Also mirror to feed.rugmunch.io static store
            try:
                item = {
                    "text": alert_post,
                    "story_ref": alert_id or "unknown",
                    "source": "rugbrain_shill_alerts",
                    "kind": "alert",
                }
                mirror_post_to_feed(item)
                logger.info("scam_alert_mirrored_to_static_feed id=%s", alert_id)
            except Exception as exc:
                logger.warning("scam_alert_mirror_failed err=%s", str(exc))

            return True
        else:
            logger.warning("scam_alert_feed_publish_failed id=%s", alert_id)
            return False

    except Exception as exc:
        logger.error("scam_alert_feed_error err=%s", str(exc))
        return False


async def run_scam_alerts_publisher(ctx: Any) -> int:
    """Main publisher function - tail shill alerts and publish new ones.

    Returns the number of alerts published.
    """
    # Lazy imports to avoid circular dependencies
    from rugmunchbot.channels.scheduler._core import (
        _chat_id,
    )

    # Get chat IDs
    main_chat_id = _chat_id("main") if POST_TO_TELEGRAM else None

    if not main_chat_id and not POST_TO_FEED:
        logger.debug("scam_alerts:skipped reason=no_destinations")
        return 0

    # Tail new alerts
    new_alerts = await asyncio.to_thread(_tail_shill_alerts)
    if not new_alerts:
        logger.debug("scam_alerts:no_new_alerts")
        return 0

    logger.info("scam_alerts:found_new_alerts count=%d", len(new_alerts))
    alerts_chat_id = _chat_id("alerts") if POST_TO_TELEGRAM else None
    main_chat_id = _chat_id("main") if POST_TO_TELEGRAM else None
    if not alerts_chat_id and not main_chat_id and not POST_TO_FEED:
        logger.debug("scam_alerts:skipped reason=no_destinations")
        return 0

    published_count = 0
    for alert in await rank_alerts(new_alerts):
        alert_id = alert.get("id")
        if not alert_id:
            logger.warning("scam_alerts:skipped_missing_id")
            continue

        rendered = _render_alert_post(alert)
        evaluation = evaluate_report(rendered, namespace="scam_alerts", min_words=MIN_WORDS)
        if not evaluation.allowed:
            logger.info("scam_alert_skipped reason=%s id=%s", evaluation.reason, alert_id)
            continue

        metadata = alert.get("metadata") or {}
        title = f"Shill Alert: {metadata.get('channel', 'unknown')} (Risk {severity(alert)})"

        for dest in destinations_for(alert):
            # per-destination ledger: one alert may legitimately reach alerts
            # AND main, but never the same destination twice.
            key = f"{dest}:{alert_id}"
            if await is_published("scam_alerts", key):
                continue
            sent = False
            chat_id = main_chat_id if dest == "main" else alerts_chat_id
            if chat_id:
                sent = await _publish_alert_to_telegram(ctx.bot, chat_id, rendered, alert_id)
            if POST_TO_FEED:
                sent = await _publish_alert_to_feed(rendered, title, alert_id) or sent
            if sent:
                await mark_published("scam_alerts", key)
                published_count += 1
                logger.info(
                    "scam_alert_published id=%s dest=%s risk=%d",
                    alert_id,
                    dest,
                    severity(alert),
                )
            else:
                logger.warning("scam_alert_publish_failed id=%s dest=%s", alert_id, dest)

    logger.info("scam_alerts:publishing_complete published=%d", published_count)
    return published_count


__all__ = ["destinations_for", "rank_alerts", "run_scam_alerts_publisher", "severity"]
