"""Publication boundary: quiet hours, rate limit, one card per entity/day.

Sending goes through ``scheduler._core._safe_send`` — the single chokepoint
that enforces the content contract, the format law and the retry budget. This
module only decides *whether* to send and supplies markup; it never opens a
second transport.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels.shill_alerts import config
from rugmunchbot.channels.shill_alerts.card import inline_markup, render_alert
from rugmunchbot.channels.shill_alerts.dedupe import AlertLedger, AlertRecord
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from rugmunchbot.channels.shill_alerts.models import Alert

logger = get_logger("rmi_bot.channels.shill_alerts.deliver")

_last_send: dict[str, float] = {}


async def _send_actions(
    bot: Any, chat_id: str, alert: Alert, buttons: list[list[tuple[str, str]]]
) -> None:
    """Post the operator action row (fail-open — the card already landed)."""
    markup = inline_markup(buttons)
    if markup is None:
        return
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=f"⚖️ Operator actions — <code>{alert.key}</code>",
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=markup,
        )
    except Exception as exc:  # buttons are an affordance, never a blocker
        logger.warning("shill_alerts:actions_markup_failed err=%s", str(exc)[:120])


def in_quiet_hours(now: datetime | None = None, spec: str | None = None) -> bool:
    """True when ``now`` (UTC) falls inside the quiet window, e.g. ``22-06``.

    Wraps midnight. An empty/malformed spec never silences the lane.
    """
    window = (spec if spec is not None else config.QUIET_HOURS).strip()
    if not window or "-" not in window:
        return False
    try:
        start, end = (int(part) for part in window.split("-", 1))
    except ValueError:
        return False
    hour = (now or datetime.now(UTC)).hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


async def deliver_alerts(
    bot: Any,
    alerts: list[Alert],
    *,
    chat_id: str | None,
    ledger: AlertLedger | None = None,
    now: datetime | None = None,
    send: Any = None,
) -> dict[str, Any]:
    """Post at most ``MAX_PER_RUN`` cards; return a per-run report.

    Every sighting is recorded even when the card is skipped, so a restart
    replay adds sources without re-posting. A failed send is NOT recorded as
    alerted — the next tick retries it. ``send`` is the DI seam for tests; in
    production it is ``scheduler._core._safe_send``, the single chokepoint.
    """
    ledger = ledger or AlertLedger()
    report: dict[str, Any] = {"posted": 0, "held": 0, "duplicate": 0, "failed": 0, "sent": []}
    if not alerts:
        return report

    if in_quiet_hours(now):
        report["held"] = len(alerts)
        report["reason"] = "quiet_hours"
        logger.info("shill_alerts:quiet_hours alerts=%s", len(alerts))
        return report

    if not chat_id or bot is None:
        report["held"] = len(alerts)
        report["reason"] = "no_channel"
        return report

    from rugmunchbot.channels.scheduler._core import _safe_send

    sender = send or _safe_send

    for alert in alerts:
        if report["posted"] >= config.MAX_PER_RUN:
            report["held"] += 1
            continue
        record = AlertRecord(
            entity=alert.entity,
            day=alert.day,
            channel=alert.channels[0] if alert.channels else "",
            message_id=alert.key,
            source=alert.sources[0] if alert.sources else "",
            confidence=alert.verdict.confidence,
            route=alert.verdict.route,
        )
        if ledger.suppressed(alert.entity):
            continue
        if ledger.alerted(alert.key):
            ledger.record(record, post=False)
            report["duplicate"] += 1
            continue

        waited = time.monotonic() - _last_send.get(chat_id, 0.0)
        if waited < config.MIN_INTERVAL_S:
            report["held"] += 1
            continue

        text, buttons = render_alert(alert)
        sent = await sender(bot, chat_id, text, content_type="alert")
        if sent:
            # Buttons ride a separate short message: ``_safe_send`` is the
            # text chokepoint and takes no markup (same split as the main
            # approval gate, which also posts its keyboard directly).
            await _send_actions(bot, chat_id, alert, buttons)
        if not sent:
            report["failed"] += 1
            logger.warning("shill_alerts:send_failed entity=%s", alert.entity[:16])
            continue
        _last_send[chat_id] = time.monotonic()
        ledger.record(record, post=True)
        report["posted"] += 1
        report["sent"].append({"entity": alert.entity, "key": alert.key})
        logger.info(
            "shill_alerts:posted entity=%s conf=%s families=%s sources=%s",
            alert.entity[:16],
            alert.verdict.confidence,
            len(alert.verdict.families),
            len(alert.sources),
        )
    return report
