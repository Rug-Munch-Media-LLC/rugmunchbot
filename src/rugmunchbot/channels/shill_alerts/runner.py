"""Entry point: detect → gate → deliver (main) / record (review queue).

Nothing here re-implements detection, transport or dedupe; it wires the
existing sensor (``sweep_channels``), the pure scorer (``plan_alerts``), the
existing scanner evidence (``shill_watch.build_scanner_evidence``) and the
single send chokepoint (``_safe_send`` via ``deliver_alerts``).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from rugmunchbot.channels.shill_alerts import config
from rugmunchbot.channels.shill_alerts.card import render_review_line
from rugmunchbot.channels.shill_alerts.dedupe import AlertLedger, today
from rugmunchbot.channels.shill_alerts.deliver import deliver_alerts
from rugmunchbot.channels.shill_alerts.plan import plan_alerts
from rugmunchbot.channels.shill_alerts.registry import load_registry
from rugmunchbot.channels.shill_alerts.sweep import sweep_channels
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.shill_alerts.runner")


def _cadence(rows: list[dict[str, Any]], hours: int = 6) -> dict[str, dict[str, float]]:
    """Per-channel rate vs median baseline — the cadence-spike signal input."""
    counts = Counter(str(m.get("channel") or "") for m in rows)
    rates = {c: round(n / max(1, hours), 2) for c, n in counts.items() if c}
    if not rates:
        return {}
    ordered = sorted(rates.values())
    baseline = ordered[len(ordered) // 2]
    return {c: {"rate": r, "baseline": baseline} for c, r in rates.items()}


async def _onchain(entity: str) -> dict[str, Any]:
    """Scanner evidence for one address (``{}`` when not addressable/down)."""
    if len(entity) < 32:
        return {}
    try:
        from rugmunchbot.channels.shill_watch import build_scanner_evidence

        chain = "ethereum" if entity.startswith("0x") else "solana"
        evidence = await build_scanner_evidence(entity, chain)
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_alerts:scanner_failed err=%s", str(exc)[:120])
        return {}
    if not evidence:
        return {}
    return {
        "onchain_risk": {
            "risk_level": evidence.get("risk_level"),
            "note": evidence.get("note"),
        }
    }


def _write_review(alerts: list[Any]) -> int:
    """Append below-gate items to the review queue; returns rows written."""
    if not alerts:
        return 0
    written = 0
    try:
        path = Path(config.REVIEW_LEDGER)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for alert in alerts:
                handle.write(
                    json.dumps(
                        {
                            "day": alert.day,
                            "entity": alert.entity,
                            "chain": alert.chain,
                            "confidence": alert.verdict.confidence,
                            "families": list(alert.verdict.families),
                            "signals": [s.name for s in alert.signals],
                            "sources": list(alert.sources[:10]),
                            "line": render_review_line(alert),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                written += 1
    except OSError as exc:
        logger.warning("shill_alerts:review_write_failed err=%s", str(exc)[:120])
    return written


async def run_shill_alert_scan(
    ctx: Any = None, *, messages: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Run one detection tick. Returns a report; never raises into the job loop.

    ``messages`` lets a caller (or a test) supply already-read rows instead of
    hitting Telegram. Everything external is fail-open.
    """
    report: dict[str, Any] = {
        "day": today(),
        "enabled": config.ENABLED,
        "scanned": 0,
        "alerts": 0,
        "main": 0,
        "review": 0,
        "posted": 0,
    }
    if not config.ENABLED:
        return report

    rows = messages if messages is not None else await sweep_channels()
    report["scanned"] = len(rows)
    if not rows:
        return report

    registry = {c.handle: c for c in load_registry()}
    known_bad = [h for h, c in registry.items() if c.shill_prone]
    cadence = _cadence(rows)
    cache: dict[str, dict[str, Any]] = {}

    def publish_channel(entity: str) -> str:
        counts = Counter(
            str(m.get("channel") or "")
            for m in rows
            if entity and entity in str(m.get("text") or "")
        )
        return counts.most_common(1)[0][0] if counts else ""

    def evidence_for(entity: str) -> dict[str, Any]:
        channel = publish_channel(entity)
        stats = cadence.get(channel) or {}
        return {
            **cache.get(entity, {}),
            "known_bad_actors": known_bad,
            "channel_rate_per_hour": stats.get("rate"),
            "channel_baseline_per_hour": stats.get("baseline"),
        }

    # Pre-resolve on-chain risk for every address seen in this tick.
    for entity in {str(m.get("entity") or "") or "" for m in rows}:
        if len(entity) >= 32:
            cache[entity] = await _onchain(entity)

    alerts = plan_alerts(rows, evidence_for=evidence_for, min_confidence=config.MIN_CONFIDENCE)
    main_alerts = [a for a in alerts if a.verdict.is_main]
    review_alerts = [a for a in alerts if not a.verdict.is_main]
    report["alerts"] = len(alerts)
    report["main"] = len(main_alerts)
    report["review"] = _write_review(review_alerts)

    bot = getattr(ctx, "bot", None) if ctx is not None else None
    chat_id = None
    if bot is not None:
        from rugmunchbot.channels.scheduler._core import _chat_id

        chat_id = _chat_id(config.CHANNEL_ROLE)

    report.update(await deliver_alerts(bot, main_alerts, chat_id=chat_id, ledger=AlertLedger()))
    logger.info("shill_alerts:run_done %s", report)
    return report
