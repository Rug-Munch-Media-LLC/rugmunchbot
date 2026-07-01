"""Professional shareable win cards — real graphical output with logo, gauge, findings.
Generates polished JPEGs users WANT to share on Twitter/X.
"""
import io
import logging

import httpx
import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, ContextTypes

logger = logging.getLogger("telegram.win_card")

RMI = {"bg":"#0f0f1a","card":"#1a1a2e","accent":"#7c4dff","green":"#00c853","red":"#ff1744","gold":"#ffd700","text":"#e0e0e0","sub":"#888888"}
LOGO_PATH = "/root/backend/app/static/rmi_logo.png"

def _gauge(ax, score: float):
    """Draw a professional risk gauge (semi-circle)."""
    theta = np.linspace(np.pi, 0, 100)
    r = 0.8
    x_inner = 0.5 + (r - 0.15) * np.cos(theta)
    y_inner = 0.5 + (r - 0.15) * np.sin(theta)
    x_outer = 0.5 + r * np.cos(theta)
    y_outer = 0.5 + r * np.sin(theta)

    # Background arc
    ax.fill_betweenx(y_inner, x_outer, x_inner, color="#333355", alpha=0.3)

    # Colored arc based on score
    score_theta = np.pi * (1 - score / 100)
    score_idx = int((1 - score / 100) * 100)
    if score_idx >= len(theta): score_idx = len(theta) - 1

    if score_idx > 0:
        fill_theta = theta[:score_idx]
        fill_x_inner = 0.5 + (r - 0.15) * np.cos(fill_theta)
        fill_y_inner = 0.5 + (r - 0.15) * np.sin(fill_theta)
        fill_x_outer = 0.5 + r * np.cos(fill_theta)
        fill_y_outer = 0.5 + r * np.sin(fill_theta)

        color = RMI["green"] if score < 20 else "#ffea00" if score < 40 else "#ff9100" if score < 60 else RMI["red"] if score < 80 else "#ff0055"
        ax.fill_betweenx(fill_y_inner, fill_x_outer, fill_x_inner, color=color, alpha=0.9)

    # Score text
    ax.text(0.5, 0.65, str(score), ha="center", va="center", fontsize=28, fontweight="bold", color="white")

    # Tick marks
    for val in [0, 25, 50, 75, 100]:
        a = np.pi * (1 - val / 100)
        tx = 0.5 + (r + 0.12) * np.cos(a)
        ty = 0.5 + (r + 0.12) * np.sin(a)
        ax.text(tx, ty, str(val), ha="center", va="center", fontsize=5, color=RMI["sub"])

def generate_win_card(token: str, chain: str, risk_score: int, risk_tier: str, findings: list) -> bytes:
    """Professional win card — users WANT to share this on social media."""
    fig = plt.figure(figsize=(6, 8), facecolor=RMI["bg"])

    # Main card background
    card = mpatches.FancyBboxPatch((0.15, 0.15), 5.7, 7.7, boxstyle="round,pad=0.08",
                                     facecolor=RMI["card"], edgecolor=RMI["accent"], linewidth=1.5)
    fig.patches.append(card)

    # Gradient header
    header = mpatches.FancyBboxPatch((0.15, 6.5), 5.7, 1.3, boxstyle="round,pad=0.08",
                                       facecolor="#12122a", edgecolor="none")
    fig.patches.append(header)

    # Logo (left side)
    try:
        logo = plt.imread(LOGO_PATH)
        fig.figimage(logo, xo=40, yo=660, alpha=1, zorder=10)
    except:
        fig.text(0.15, 0.88, "🛡", fontsize=24, ha="center", va="center", color=RMI["accent"])

    # Title
    fig.text(0.35, 0.875, "RUG MUNCH BOT", fontsize=13, fontweight="bold", color="white", ha="left")
    fig.text(0.35, 0.85, "Token Security Scanner", fontsize=8, color=RMI["sub"], ha="left")

    # Risk gauge (center)
    gax = fig.add_axes([0.28, 0.48, 0.44, 0.3])
    gax.set_xlim(0, 1)
    gax.set_ylim(0, 1)
    gax.axis("off")
    _gauge(gax, risk_score)

    # Risk tier label
    tier_colors = {"safe":RMI["green"],"low":"#ffea00","medium":"#ff9100","high":RMI["red"],"critical":"#ff0055"}
    tc = tier_colors.get(risk_tier.lower(), RMI["sub"])
    fig.text(0.5, 0.46, f"RISK: {risk_tier.upper()}", fontsize=11, fontweight="bold", color=tc, ha="center")

    # Token address
    short = token[:8] + "..." + token[-4:] if len(token) > 16 else token
    fig.text(0.5, 0.42, f"{short} · {chain.upper()}", fontsize=9, color=RMI["text"], ha="center", family="monospace")

    # Findings section header
    fig.text(0.5, 0.38, "🚩 FINDINGS", fontsize=8, fontweight="bold", color=RMI["gold"], ha="center")

    # Findings
    y = 0.345
    for f in findings[:5]:
        f_short = f[:42] + ".." if len(f) > 42 else f
        fig.text(0.5, y, f"⚠ {f_short}", fontsize=7, color=RMI["text"], ha="center")
        y -= 0.035

    # Divider
    fig.text(0.5, 0.17, "─" * 40, fontsize=6, color=RMI["sub"], ha="center")

    # Stats footer
    risk_text = f"{risk_score}/100 · {risk_tier.upper()}"
    fig.text(0.5, 0.14, f"📊 {risk_text} · {len(findings)} flags", fontsize=7, color=RMI["sub"], ha="center")

    # Share prompt
    fig.text(0.5, 0.10, "🔍 Scan any token → @rugmunchbot", fontsize=8, color=RMI["accent"], ha="center", fontweight="bold")
    fig.text(0.5, 0.07, "🐦 Share on Twitter/X", fontsize=7, color=RMI["gold"], ha="center")

    buf = io.BytesIO()
    fig.savefig(buf, format="jpeg", dpi=200, facecolor=RMI["bg"], bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()

async def win_card_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "🏆 *Win Card*\n\nShare your scam predictions on Twitter/X.\n"
            "Generates a professional branded JPEG.\n\n"
            "Usage: `/wincard <address>`\n\n"
            "Example: `/wincard So11111111111111111111111111111111111111112`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    address = context.args[0]
    chain = "ethereum" if address.startswith("0x") else "tron" if address.startswith("T") else "solana"

    msg = await update.message.reply_text("🏆 Generating win card...", parse_mode=ParseMode.MARKDOWN)

    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post("http://127.0.0.1:8000/scanner/scan", json={
                "chain": chain, "address": address, "depth": "quick"
            })
            if r.status_code != 200:
                await msg.edit_text(f"❌ Scan failed: {r.status_code}")
                return

            data = r.json()
            risk = data.get("risk_score", 50)
            tier = data.get("risk_tier", "unknown")
            findings = data.get("findings", [])

            png = generate_win_card(address, chain, risk, tier, findings)

            share = f"🛡 I scanned a token with @rugmunchbot!\nRisk: {risk}/100 ({tier.upper()})\n"
            if findings: share += f"{findings[0]}\n"
            share += "\nScan: t.me/rugmunchbot"
            url = f"https://twitter.com/intent/tweet?text={share.replace(' ','%20').replace('\n','%0A')}"

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🐦 Share on Twitter/X", url=url)],
            ])

            await msg.delete()
            await update.message.reply_photo(
                photo=png,
                caption=f"🏆 *Win Card*\nRisk: {risk}/100 · {tier.upper()}\n{len(findings)} flags detected",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb,
            )
    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)[:100]}")

handlers = [CommandHandler("wincard", win_card_command)]
