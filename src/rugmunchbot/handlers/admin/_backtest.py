#!/usr/bin/env python3
"""/backtest — @rmi_admin_bot scanner backtest command (owner-only).

Calls the rmi-backend scanner backtest endpoint and reports
precision / recall / F1 with a confusion matrix.
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

logger = get_logger("rmi_bot.admin.backtest")

#: Backtest can take a while (it replays historical scans).
_TIMEOUT = 120.0


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


async def backtest_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/backtest — run scanner backtest, show precision/recall/F1 (owner only)."""
    user = update.effective_user
    if user is None or not is_owner(user.id):
        logger.warning("admin_cmd_denied cmd=backtest user=%s", user.id if user else None)
        await _reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    from rugmunchbot.config import BACKEND_URL, backend_headers

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                f"{BACKEND_URL}/api/v1/scanner/backtest",
                headers=backend_headers(),
            )
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.warning("admin_backtest_failed err=%s", str(exc)[:120])
        await _reply(update, "\u274c Backend unreachable — backtest failed.")
        return

    if r.status_code != 200:
        logger.warning("admin_backtest_http status=%d", r.status_code)
        await _reply(update, f"\u274c Backtest failed (HTTP {r.status_code}).")
        return

    try:
        data: dict[str, Any] = r.json()
    except ValueError:
        await _reply(update, "\u274c Backtest returned invalid JSON.")
        return

    metrics = data.get("metrics", data)
    cm = data.get("confusion_matrix", {})

    lines = [
        "\U0001f9ee <b>Scanner Backtest</b>",
        "\u2500" * 20,
        f"\U0001f3af Precision: <b>{_fmt_pct(metrics.get('precision'))}</b>",
        f"\U0001f50d Recall: <b>{_fmt_pct(metrics.get('recall'))}</b>",
        f"\u2696\ufe0f F1: <b>{_fmt_pct(metrics.get('f1'))}</b>",
    ]

    tp = cm.get("tp", cm.get("true_positives"))
    fp = cm.get("fp", cm.get("false_positives"))
    fn = cm.get("fn", cm.get("false_negatives"))
    tn = cm.get("tn", cm.get("true_negatives"))
    if any(v is not None for v in (tp, fp, fn, tn)):
        lines.append("\u2500" * 20)
        lines.append("<b>Confusion Matrix</b>")
        lines.append(f"  TP: <b>{html.escape(str(tp))}</b>  FP: <b>{html.escape(str(fp))}</b>")
        lines.append(f"  FN: <b>{html.escape(str(fn))}</b>  TN: <b>{html.escape(str(tn))}</b>")

    if data.get("sample_size") is not None:
        lines.append(f"\U0001f4ca Sample: <b>{html.escape(str(data['sample_size']))}</b>")

    await _reply(update, "\n".join(lines))
