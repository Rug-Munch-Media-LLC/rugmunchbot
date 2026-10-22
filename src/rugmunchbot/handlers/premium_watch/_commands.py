"""/watch, /unwatch, /watchlist, /export — the paid watch surface.

Every command answers in one message and never leaves the user guessing about
state: a watch reply says what will be alerted and how many chats watch it, a
refusal says the quota and how to free a slot, and an export is the exact
bytes the webhook carries (same schema, same version).

Chain is inferred from the address shape when omitted; an address that looks
like neither Solana nor EVM is rejected before any backend call.
"""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

from telegram import InputFile
from telegram.constants import ParseMode

from rugmunchbot import db
from rugmunchbot.formatters import detect_chain, footer_links, short_addr
from rugmunchbot.handlers.premium_watch import _api, _limits

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

_EVENTS = (
    ("cashed_out", "a top holder's share collapses"),
    ("lp_unlocked", "the LP lock disappears or liquidity halves"),
    ("new_bundle_wallet", "a wallet joins the bundled set"),
    ("authority_changed", "mint / freeze / owner moves"),
)


def _target(args: list[str]) -> tuple[str, str] | None:
    """Address and chain, or ``None`` when the address is unusable."""
    address = args[0].strip()
    chain = (args[1].strip().lower() if len(args) > 1 else detect_chain(address))
    return None if chain in ("", "unknown") else (address, chain)


async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Subscribe this chat to a token's deltas."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    target = _target(ctx.args or [])
    if target is None:
        await message.reply_text(
            "👀 Usage: /watch &lt;address&gt; [chain] [symbol]\n\n"
            "Example: /watch <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    address, chain = target
    symbol = (ctx.args[2].upper() if len(ctx.args or []) > 2 else "")
    chat_id = str(user.id)
    tier = db.get_user_tier(user.id)
    limit = _limits.watch_limit(tier)
    try:
        existing = await _api.list_watches(chat_id)
        watched = any(row.get("token") == address and row.get("chain") == chain for row in existing)
        if not watched and len(existing) >= limit:
            await message.reply_text(_limits.limit_refusal(len(existing), limit), parse_mode=ParseMode.HTML)
            return
        result = await _api.watch_token(address, chain, chat_id)
    except _api.PremiumWatchError as exc:
        await message.reply_text(f"❌ Could not watch that token: {exc}")
        return
    title = symbol or short_addr(address)
    events = "\n".join(f"• <code>{kind}</code> — {text}" for kind, text in _EVENTS)
    await message.reply_text(
        f"👀 <b>Watching {title}</b>\n"
        f"<code>{address}</code> · {chain} · {int(result.get('watchers_n') or 1)} watching\n\n"
        f"<b>You get an alert when:</b>\n{events}\n\n"
        f"<i>Only real transitions alert — every card carries the before/after numbers.</i>\n"
        f"Stop: /unwatch <code>{address}</code> · List: /watchlist\n\n{footer_links()}",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def cmd_unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop watching one token, reporting whether it existed."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    target = _target(ctx.args or [])
    if target is None:
        await message.reply_text(
            "Usage: /unwatch <code>address</code> [chain]", parse_mode=ParseMode.HTML
        )
        return
    address, chain = target
    try:
        result = await _api.unwatch_token(address, chain, str(user.id))
    except _api.PremiumWatchError as exc:
        await message.reply_text(f"❌ Could not remove that watch: {exc}")
        return
    if not result.get("removed"):
        await message.reply_text(
            f"Not watching <code>{address}</code> — nothing to remove.", parse_mode=ParseMode.HTML
        )
        return
    await message.reply_text(
        f"✅ Stopped watching <code>{address}</code> — "
        f"{int(result.get('watchers_n') or 0)} chat(s) still watching.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """List this chat's watches against its quota."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    try:
        rows = await _api.list_watches(str(user.id))
    except _api.PremiumWatchError as exc:
        await message.reply_text(f"❌ Could not load your watchlist: {exc}")
        return
    tier = db.get_user_tier(user.id)
    limit = _limits.watch_limit(tier)
    if not rows:
        await message.reply_text(
            f"👀 <b>No watches yet</b> — 0/{limit} on {tier}.\n\n"
            f"/watch <code>address</code> to start. You get a card only when "
            "holders, liquidity, bundles or authorities actually move.",
            parse_mode=ParseMode.HTML,
        )
        return
    lines = [f"👀 <b>Your watches</b> — {len(rows)}/{limit} ({tier})\n"]
    for row in rows:
        token = str(row.get("token") or "")
        lines.append(
            f"• <code>{short_addr(token)}</code> · {row.get('chain') or '?'} · "
            f"{int(row.get('watchers_n') or 0)} watching\n"
            f"  /unwatch <code>{token}</code>"
        )
    lines.append("\n/export <code>address</code> for the full JSON dump.")
    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the canonical scan export as a JSON document."""
    message = update.effective_message
    if message is None:
        return
    target = _target(ctx.args or [])
    if target is None:
        await message.reply_text(
            "Usage: /export <code>address</code> [chain]\n\n"
            "Sends the same payload a webhook subscriber receives.",
            parse_mode=ParseMode.HTML,
        )
        return
    address, chain = target
    note = await message.reply_text("📦 Building the export…")
    try:
        body = await _api.export_scan(address, chain)
    except _api.PremiumWatchError as exc:
        await note.edit_text(f"❌ Export failed: {exc}")
        return
    filename = f"scan_{chain}_{short_addr(address).replace('...', '_')}.json"
    await message.reply_document(
        document=InputFile(BytesIO(body), filename=filename),
        caption=(
            f"📦 <code>{address}</code> · {chain} · {len(body):,} bytes\n"
            "<i>schema premium.scan.v1 — identical to the webhook body.</i>"
        ),
        parse_mode=ParseMode.HTML,
    )
    await note.delete()
