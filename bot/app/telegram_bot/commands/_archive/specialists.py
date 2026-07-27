"""Specialist command handlers for rugmunchbot — whale, ask, compliance, report."""

# ⚠️ DEAD CODE — NOT WIRED INTO MAIN BOT
# This module is NOT imported or registered by bot.py.
# Kept as reference for future migration to rmi-backend.
# DO NOT add production credentials to this file.

import logging
import os

import httpx
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("rmi.specialists")
BACKEND = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")


async def cmd_whale(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """🐋 Track a whale wallet — /whale <address> [chain]"""
    if not ctx.args:
        await update.message.reply_text(
            "Usage: /whale <wallet_address> [chain]\nExample: /whale 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
        )
        return
    addr = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else "eth"
    await update.message.reply_text(
        f"🐋 Tracking whale {addr[:10]}...{addr[-6:]} on {chain}..."
    )
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"{BACKEND}/api/v1/whale/{addr}", params={"chain": chain}, timeout=15
            )
            if r.status_code == 200:
                data = r.json()
                msg = "🐋 <b>Whale Report</b>\n\n"
                msg += f"Balance: ${data.get('total_usd', 0):,.0f}\n"
                msg += f"Tokens: {data.get('token_count', 0)}\n"
                msg += f"Recent txs (24h): {data.get('tx_24h', 0)}\n"
                if data.get("labels"):
                    msg += f"\nLabels: {', '.join(data['labels'][:5])}"
                await update.message.reply_text(msg, parse_mode="HTML")
            else:
                await update.message.reply_text(
                    f"Whale data unavailable ({r.status_code})"
                )
    except Exception as e:
        await update.message.reply_text(f"Error tracking whale: {e}")


async def cmd_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """🧠 Ask RMI anything — /ask <question>"""
    if not ctx.args:
        await update.message.reply_text(
            "Usage: /ask <your question>\nExample: /ask what's the latest on Ethereum?"
        )
        return
    question = " ".join(ctx.args)
    await update.message.reply_text("🧠 Searching knowledge base...")
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{BACKEND}/api/v1/rag/query", json={"query": question}, timeout=30
            )
            if r.status_code == 200:
                data = r.json()
                answer = data.get("answer", data.get("response", "No answer found"))
                sources = data.get("sources", [])
                msg = f"<b>Answer:</b>\n{answer[:800]}"
                if sources:
                    msg += f"\n\n📚 <i>Sources: {len(sources)} documents</i>"
                await update.message.reply_text(msg, parse_mode="HTML")
            else:
                await update.message.reply_text(
                    f"Knowledge base unavailable ({r.status_code}). Try /scan instead."
                )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_compliance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """🛡️ Check address compliance — /compliance <address>"""
    if not ctx.args:
        await update.message.reply_text("Usage: /compliance <wallet_address>")
        return
    addr = ctx.args[0]
    await update.message.reply_text(
        f"🛡️ Checking {addr[:10]}...{addr[-6:]} for sanctions..."
    )
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{BACKEND}/api/v1/compliance/{addr}", timeout=15)
            if r.status_code == 200:
                data = r.json()
                score = data.get("risk_score", 0)
                flags = data.get("flags", [])
                bar = risk_bar(score)
                msg = f"🛡️ <b>Compliance Check</b>\n{bar} {score}/100\n\n"
                if flags:
                    msg += f"🚩 Flags: {len(flags)}\n" + "\n".join(
                        f"  • {f}" for f in flags[:3]
                    )
                else:
                    msg += "✅ No sanctions or compliance flags detected"
                await update.message.reply_text(msg, parse_mode="HTML")
            else:
                await update.message.reply_text(
                    f"Compliance check unavailable ({r.status_code})"
                )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """📊 Generate research report — /report <token_address>"""
    if not ctx.args:
        await update.message.reply_text(
            "Usage: /report <token_address>\nGenerates a deep-dive research report."
        )
        return
    addr = ctx.args[0]
    await update.message.reply_text(
        f"📊 Generating research report for {addr[:10]}...{addr[-6:]}... (may take 30s)"
    )
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{BACKEND}/api/v1/report/generate", json={"address": addr}, timeout=60
            )
            if r.status_code == 200:
                data = r.json()
                report = data.get("report", data.get("summary", "Report unavailable"))
                await update.message.reply_text(
                    f"📊 <b>Research Report</b>\n\n{report[:3500]}", parse_mode="HTML"
                )
            else:
                await update.message.reply_text(
                    f"Report generation failed ({r.status_code})"
                )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


def risk_bar(score: int) -> str:
    """Visual risk bar."""
    filled = max(1, min(10, score // 10))
    empty = 10 - filled
    color = "🟢" if score < 30 else "🟡" if score < 60 else "🟠" if score < 80 else "🔴"
    return color + "━" * filled + "─" * empty
