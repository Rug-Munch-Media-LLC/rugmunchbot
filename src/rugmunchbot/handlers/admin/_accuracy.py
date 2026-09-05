#!/usr/bin/env python3
"""/accuracy — @rmi_admin_bot scanner accuracy stats command (owner-only).

Shows precision, recall, F1 and false positive/negative rates from the
last backtest run recorded by rmi-backend.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any

import httpx
from telegram.constants import ParseMode

from rugmunchbot.states import is_owner
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.admin.accuracy")

_TIMEOUT = 15.0


async def _reply(update: Update, text: str) -> None:
    """Send a formatted HTML reply, failing open to plain text."""
    msg = update.effective_message
    if msg is None:
        return
    try:
        await msg.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as exc:
        logger.warning("admin_reply_failed err=%s", str(exc)[:120])
        try:
            await msg.reply_text(text)
        except Exception:
            logger.warning("admin_reply_plain_failed", exc_info=True)


def _fmt_pct(value: Any) -> str:
    """Format a 0-1 ratio as a percentage string, tolerating missing data."""
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


async def accuracy_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/accuracy — show scanner accuracy stats from last backtest (owner only)."""
    user = update.effective_user
    if user is None or not is_owner(user.id):
        logger.warning("admin_cmd_denied cmd=accuracy user=%s", user.id if user else None)
        await _reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    from rugmunchbot.config import BACKEND_URL, backend_headers

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                f"{BACKEND_URL}/api/v1/scanner/accuracy",
                headers=backend_headers(),
            )
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.warning("admin_accuracy_failed err=%s", str(exc)[:120])
        await _reply(update, "\u274c Backend unreachable — accuracy stats unavailable.")
        return

    if r.status_code != 200:
        logger.warning("admin_accuracy_http status=%d", r.status_code)
        await _reply(update, f"\u274c Accuracy stats unavailable (HTTP {r.status_code}).")
        return

    try:
        data: dict[str, Any] = r.json()
    except ValueError:
        await _reply(update, "\u274c Accuracy endpoint returned invalid JSON.")
        return

    metrics = data.get("metrics", data)

    lines = [
        "\U0001f4ca <b>Scanner Accuracy</b>",
        "\u2500" * 20,
        f"\U0001f3af Precision: <b>{_fmt_pct(metrics.get('precision'))}</b>",
        f"\U0001f50d Recall: <b>{_fmt_pct(metrics.get('recall'))}</b>",
        f"\u2696\ufe0f F1: <b>{_fmt_pct(metrics.get('f1'))}</b>",
        f"\U0001f6ab False positive rate: <b>{_fmt_pct(metrics.get('false_positive_rate'))}</b>",
        f"\u26d4 False negative rate: <b>{_fmt_pct(metrics.get('false_negative_rate'))}</b>",
    ]

    if data.get("last_run") is not None:
        lines.append(f"\U0001f552 Last run: <code>{html.escape(str(data['last_run']))}</code>")

    await _reply(update, "\n".join(lines))
