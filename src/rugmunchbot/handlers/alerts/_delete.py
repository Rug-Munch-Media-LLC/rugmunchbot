"""``/alert delete <address>`` — delete one price alert for the calling user.

``cmd_alert`` forwards the ``delete`` sub-command here; the handler also
works stand-alone (it picks the address out of ``ctx.args``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from rugmunchbot.db.alerts import delete_alert
from rugmunchbot.formatters import detect_chain, footer_links
from rugmunchbot.handlers._messages import failure_card
from rugmunchbot.keyboards import back_kb
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")


def _address_from(args: list[str]) -> str:
    """Pick the first argument that parses as a chain address."""
    for raw in args:
        candidate = raw.strip()
        if candidate and detect_chain(candidate) != "unknown":
            return candidate
    return ""


async def cmd_alertdel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete the caller's alert for one token address."""
    user = update.effective_user
    if user is None:
        return
    address = _address_from(list(ctx.args or []))
    if not address:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            failure_card(
                "Which alert?",
                "Send the token address whose alert you want to delete.",
                "Example: /alert delete <code>address</code>",
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    if delete_alert(user.id, address):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"\U0001f5d1 <b>Alert Deleted</b>\n\n"
            f"Token: <code>{address}</code>\n\n"
            f"No further notifications for this rule.\n"
            f"Set a new one with /alert ABOVE|BELOW <code>address</code> <price>"
            f"{footer_links()}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
            disable_web_page_preview=True,
        )
        return

    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        failure_card(
            "No Alert Found",
            f"There is no alert on <code>{address}</code> for your account.",
            "Use /alerts to list your active rules.",
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
    )
