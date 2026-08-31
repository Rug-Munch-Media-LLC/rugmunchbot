"""Scanner-alert Telegram rail — polls RugBrain live detections → @rmialerts.

Wires the previously dead pipeline end-to-end (2026-08-31):

    scanner detection (rmi-backend)
      → Redis ``rmi:rag:ingest``
      → rugbrain ``scanner_subscriber`` → LiveThreatStore
      → GET /api/v1/alerts/recent   ← this module polls
      → bot.send_message → @rmialerts

The rugbrain API already exposes ``GET /api/v1/alerts/recent?minutes=N``
(``rugbrain.alert_routes``); this rail polls it on a short tick, dedupes per
incident (stable key = store alert id), and posts each new detection in the
alerts-channel format: severity emoji + token + verdict + top-3 red flags +
risk score + ONE source link + image when available.

Fail-open everywhere: an unreachable RugBrain lane, an empty sweep, a failed
send, or a Redis outage never crashes the tick — silence is a valid outcome.
"""

from __future__ import annotations

import contextlib
import html
import os
from typing import Any

import httpx

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.scanner_alerts")

SCANNER_ALERTS_ENABLED = os.getenv("RMI_SCANNER_ALERTS_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
SCANNER_ALERT_INTERVAL_MIN = int(os.getenv("RMI_SCANNER_ALERTS_INTERVAL_MIN", "10"))
#: Sweep window (minutes) — must exceed the tick interval so no incident is
#: missed between polls; dedupe makes the overlap safe.
SCANNER_SWEEP_MINUTES = max(SCANNER_ALERT_INTERVAL_MIN * 3, 30)
#: RugBrain API base (bot runs on HOST; rugbrain-api binds 127.0.0.1:8012).
RAG_API_URL = os.getenv("RMI_RAG_API_URL", "http://localhost:8012")
#: RugBrain API key (same env var the vendored rugbrain_client uses).
RAG_API_KEY = os.getenv("RMI_RAG_API_KEY", "")

SEVERITY_EMOJI = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}


def _esc(value: object) -> str:
    """HTML-escape for Telegram parse_mode=html."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def fetch_rag_alerts(minutes: int | None = None) -> list[dict[str, Any]]:
    """Fetch recent RugBrain alerts (scanner detections + exploit pipeline).

    Fail-open: an unreachable lane returns [] (the tick no-ops).
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{RAG_API_URL}/api/v1/alerts/recent",
                params={"minutes": minutes or SCANNER_SWEEP_MINUTES},
                headers={"X-API-Key": RAG_API_KEY} if RAG_API_KEY else {},
            )
            if r.status_code != 200:
                logger.warning("rag_alerts_fetch_failed status=%d", r.status_code)
                return []
            data = r.json()
            alerts = data.get("alerts", []) if isinstance(data, dict) else []
            return alerts if isinstance(alerts, list) else []
    except (OSError, TimeoutError, httpx.HTTPError) as exc:
        logger.debug("rag_alerts_fetch_error %s", str(exc)[:80])
        return []


def format_scanner_alert(alert: dict[str, Any]) -> str:
    """Build the HTML Telegram message for one RugBrain alert.

    Layout (alerts-channel contract): severity emoji + token + verdict +
    top-3 red flags + risk score + ONE source link. The text is always the
    message body; when the payload carries an image the caller sends it as
    a best-effort photo attachment (never duplicated into the text).
    """
    severity = str(alert.get("severity") or alert.get("risk_level") or "high").lower()
    emoji = SEVERITY_EMOJI.get(severity, "⚪")
    name = str(alert.get("name") or alert.get("title") or "Unknown token")
    symbol = str(alert.get("symbol") or "")
    chain = str(alert.get("chain") or "unknown")
    score = alert.get("safety_score", alert.get("risk_score"))
    flags = alert.get("risk_flags") or alert.get("flags") or []

    title = f"{emoji} <b>{_esc(name)}</b>"
    if symbol and symbol != name:
        title = f"{emoji} <b>{_esc(name)} ({_esc(symbol)})</b>"

    lines = [title, f"⛓️ {_esc(chain).capitalize()}"]

    if score:
        with contextlib.suppress(TypeError, ValueError):
            lines.append(f"⚠️ Risk Score: {float(score):.0f}%")

    if flags:
        flag_list = "\n".join(f"🔴 {_esc(f)}" for f in flags[:5])
        lines.append(f"What was detected:\n{flag_list}")

    url = str(alert.get("url") or "").strip()
    if url:
        lines.append(f'🔗 <a href="{html.escape(url, quote=True)}">{_esc(url)}</a>')
    return "\n".join(lines)


async def _send_photo(bot: Any, chat_id: str, image_url: str) -> bool:
    """Send one photo attachment (no caption — text goes via _safe_send).

    Raises on failure so the caller can log; the text message is the
    authoritative delivery and is sent independently.

    [REVIEW] Superseded by the 2026-08-31 photo-aware delivery
    (``channels._media.send_news_photo`` with caption); kept for
    backward-compatible imports/tests.
    """
    await bot.send_photo(
        chat_id=chat_id,
        photo=image_url,
    )
    return True


async def run_scanner_alert_watch(bot: Any) -> dict[str, Any]:
    """One scheduled sweep: poll RugBrain, post new alerts to @rmialerts.

    Dedupe: per-incident key (store alert id) through the scheduler's
    global ``_remember`` namespace — a failed send releases the mark so the
    next tick retries. Returns stats.
    """
    from rugmunchbot.channels.scheduler._core import (
        _release,
        _remember,
        _resolve_channel_id,
        _safe_send,
    )

    alerts = await fetch_rag_alerts()
    alerts_chat = _resolve_channel_id("alerts")
    if not alerts_chat:
        logger.debug("scanner_alerts_skipped reason=no_chat_id channel=alerts")
        return {"seen": len(alerts), "posted": 0, "skipped": len(alerts), "failed": 0}

    stats = {"seen": len(alerts), "posted": 0, "skipped": 0, "failed": 0}
    for alert in alerts:
        alert_id = str(alert.get("id") or "")
        if not alert_id:
            stats["skipped"] += 1
            continue
        mark = f"scanalert:{alert_id}"
        if not _remember("alerts", mark := f"scanalert:{alert_id}"):
            stats["skipped"] += 1
            continue
        text = format_scanner_alert(alert)
        # HARD quality gate (2026-08-31): lint → autofix → skip. The alert
        # format is deliberately terse (structured flags, not prose), so the
        # bland-commentary floor is relaxed here — [REVIEW] if alerts should
        # carry full 20-word commentary instead.
        from rugmunchbot.support.quality_gate import pre_post_quality_gate

        gate = await pre_post_quality_gate(
            text,
            image_url=str(alert.get("image_url") or "").strip(),
            source=alert_id[:80],
            kind="scanner_alert",
            min_words=8,
            # Image doctrine (2026-08-31): every channel post carries an
            # image — the alert payload's chart when present, else the
            # branded placeholder.
            require_image=True,
            allow_placeholder_image=True,
            logger=logger,
        )
        if not gate.ok:
            _release("alerts", mark)
            stats["skipped"] += 1
            continue
        text = gate.text
        # Photo-aware delivery (2026-08-31): the alert ships as photo +
        # caption when the gate resolved an image (payload chart or branded
        # placeholder); text-only is the fallback, never the default.
        image_url = str(gate.image_url or "").strip()
        sent = False
        if image_url.startswith(("http://", "https://")):
            try:
                from rugmunchbot.channels._media import send_news_photo

                sent = await send_news_photo(bot, alerts_chat, caption=text, photo_url=image_url)
            except Exception as exc:
                logger.debug("scanner_photo_failed %s", str(exc)[:80])
        if not sent:
            # Text message is the authoritative delivery fallback.
            sent = await _safe_send(bot, alerts_chat, text)
        if sent:
            stats["posted"] += 1
            logger.info("scanner_alert_posted id=%s", alert_id[:60])
        else:
            _release("alerts", mark)
            stats["failed"] += 1
            logger.warning("scanner_alert_delivery_failed id=%s", alert_id[:60])
    return stats


__all__ = [
    "RAG_API_KEY",
    "RAG_API_URL",
    "SCANNER_ALERTS_ENABLED",
    "SCANNER_ALERT_INTERVAL_MIN",
    "SCANNER_SWEEP_MINUTES",
    "fetch_rag_alerts",
    "format_scanner_alert",
    "run_scanner_alert_watch",
]
