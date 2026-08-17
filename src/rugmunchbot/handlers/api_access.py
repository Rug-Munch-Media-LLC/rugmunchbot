#!/usr/bin/env python3
"""
RugMunchBot -- /api command: API access token management.

Pro tier. Generate and manage API tokens for programmatic access
to RugMunch intelligence data.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    WEBSITE_URL,
)
from app.domains.telegram.rugmunchbot.formatters import (
    footer_links,
    is_owner,
    sep,
)
from app.domains.telegram.rugmunchbot.handlers._messages import failure_card
from app.domains.telegram.rugmunchbot.keyboards import api_kb, paywall_kb
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.api")


async def cmd_api(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Manage API access tokens.

    Usage: /api [action]
    Actions: create, list, revoke <id>

    Pro tier only.
    """
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    tier = db.get_user_tier(user.id)
    if tier != "pro" and not is_owner(user.id):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "🔑 <b>API Access</b>\n\n"
            "API tokens let you programmatically access:\n"
            "  • Token security scans\n"
            "  • Price & market data\n"
            "  • Wallet forensics\n"
            "  • Holder analysis\n\n"
            "🛰️ <b>Monitor (250★/30d)</b> — live LP/whale + tax/ownership/mint alerts · 🔬 <b>Deep Scan (250★)</b> — full forensics\n\n"
            "Upgrade now to unlock API access!",
            parse_mode=ParseMode.HTML,
            reply_markup=paywall_kb(),
        )
        return

    action = ctx.args[0].lower() if ctx.args else "list"

    if action == "create":
        await _create_token(update, user.id)
    elif action == "list":
        await _list_tokens(update, user.id)
    elif action == "revoke" and len(ctx.args or []) > 1:
        try:
            token_id = int((ctx.args or [])[1])
            await _revoke_token(update, user.id, token_id)
        except (ValueError, TypeError):
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                failure_card(
                    "Invalid Token ID",
                    "The API token ID must be a number.",
                    "Usage: /api revoke <code>id</code> — find IDs with /api list",
                ),
                parse_mode=ParseMode.HTML,
            )
    else:
        # Show current token status
        conn = db.get_db()
        tokens = conn.execute(
            "SELECT * FROM api_tokens WHERE user_id = ? AND is_active = 1 ORDER BY created_at DESC",
            (user.id,),
        ).fetchall()
        conn.close()

        if tokens:
            text = (
                "🔑 <b>API Access</b>\n" + sep() + "\n\n"
                f"You have <b>{len(tokens)}</b> active API token(s).\n\n"
                "<b>Commands:</b>\n"
                "  /api create — Generate new token\n"
                "  /api list — Show all tokens\n"
                "  /api revoke <code>id</code> — Revoke a token\n\n"
                f"<b>API Base URL:</b>\n<code>{BACKEND_URL}/api/v1</code>\n\n"
                "Use your token in the <code>Authorization: Bearer &lt;token&gt;</code> header."
            )
        else:
            text = (
                "🔑 <b>API Access</b>\n" + sep() + "\n\n"
                "No API tokens yet. Generate one:\n"
                "  /api create\n\n"
                "Your API token gives you programmatic access to all "
                "Rug Munch intelligence data.\n\n"
                f"<b>API Base URL:</b>\n<code>{BACKEND_URL}/api/v1</code>\n\n"
                f"<b>Documentation:</b>\n<code>{WEBSITE_URL}/docs</code>"
            )
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=api_kb(has_token=bool(tokens)),
            disable_web_page_preview=True,
        )


async def _create_token(update: Update, user_id: int) -> None:
    """Generate a new API token."""
    # Check limit: Pro gets 3 tokens
    conn = db.get_db()
    count = conn.execute(
        "SELECT COUNT(*) as c FROM api_tokens WHERE user_id = ? AND is_active = 1",
        (user_id,),
    ).fetchone()["c"]

    if count >= 3:
        conn.close()
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            failure_card(
                "Token Limit Reached",
                "Maximum 3 active API tokens.",
                "Revoke one with /api revoke <code>id</code>, or see /api list.",
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    # Generate secure token
    raw_token = f"rmi_{secrets.token_hex(24)}"
    # Only the sha256 digest is stored at rest; the raw token is shown to the
    # user exactly once below and is never recoverable afterwards.
    hashed_token = db.hash_api_token(raw_token)
    token_prefix = db.token_prefix(raw_token)

    conn.execute(
        "INSERT INTO api_tokens (user_id, token, token_prefix, name, is_active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
        (user_id, hashed_token, token_prefix, f"Token #{count + 1}", int(__import__("time").time())),
    )
    conn.commit()
    token_id = conn.lastrowid  # type: ignore[attr-defined]  # PTB None-able fields
    conn.close()

    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"✅ <b>API Token Generated!</b>\n\n"
        f"<b>Token:</b>\n"
        f"<code>{raw_token}</code>\n\n"
        f"⚠️ <b>Save this now!</b> You won't be able to see it again.\n\n"
        f"<b>Usage:</b>\n"
        f'  <code>curl -H "Authorization: Bearer {raw_token}" \\</code>\n'
        f"  <code>  {BACKEND_URL}/api/v1/scanner/token/scan</code>\n\n"
        f"<b>Token ID:</b> #{token_id}\n"
        f"Revoke: /api revoke {token_id}\n"
        f"View all: /api list\n\n"
        f"📖 <a href='{WEBSITE_URL}/docs'>API Documentation</a>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def _list_tokens(update: Update, user_id: int) -> None:
    """Show all API tokens."""
    conn = db.get_db()
    tokens = conn.execute(
        "SELECT * FROM api_tokens WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()

    if not tokens:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "🔑 <b>API Tokens</b>\n\nNo tokens yet. Generate one:\n  /api create",
            parse_mode=ParseMode.HTML,
        )
        return

    text = "🔑 <b>API Tokens</b>\n" + sep() + "\n\n"
    for t in tokens:
        tok = dict(t)
        status = "✅ Active" if tok.get("is_active") else "❌ Revoked"
        last = ""
        if tok.get("last_used"):
            last = f"\n  📡 Last used: {tok['last_used']}"
        name = tok.get("name", f"Token #{tok['id']}")
        prefix = tok.get("token_prefix") or ""
        token_preview = f"{prefix}..." if prefix else "***"
        text += f"<b>#{tok['id']}</b> — {name}\n  🔑 <code>{token_preview}</code>\n  {status}{last}\n\n"
    text += "Create new: /api create\nRevoke: /api revoke <code>id</code>"
    text += footer_links()
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def _revoke_token(update: Update, user_id: int, token_id: int) -> None:
    """Revoke an API token."""
    conn = db.get_db()
    row = conn.execute(
        "SELECT * FROM api_tokens WHERE id = ? AND user_id = ?",
        (token_id, user_id),
    ).fetchone()

    if not row:
        conn.close()
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            failure_card(
                "Token Not Found",
                "No API token with that ID.",
                "Use /api list to see your tokens.",
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    conn.execute(
        "UPDATE api_tokens SET is_active = 0 WHERE id = ?",
        (token_id,),
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"✅ <b>Token #{token_id} revoked.</b>\n\nThis token can no longer access the API.",
        parse_mode=ParseMode.HTML,
    )
