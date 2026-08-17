"""
🧠 /dune — On-Chain Intel from curated PUBLIC Dune queries.

Real data only: every figure comes from Dune's cached-results API for verified
public query IDs (see app.databus.provider_implementations.dune). When a query
is private / expired / failed the card shows "—"; nothing is ever fabricated.

Fetches the digest from the backend premium route
(``/api/v1/market/dune``) and falls back to calling the Dune provider directly
when the backend is unreachable.
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools.registry import backend_json_get

logger = get_logger("rmi.dune")

# Row-limit shown per query in the card.
ROW_LIMIT = 4
MAX_ROW_LEN = 110

# Rendering order + section titles for the curated queries.
SECTIONS: list[tuple[str, str]] = [
    ("dex_trades", "🔄 DEX Trades"),
    ("whale_transactions", "🦈 Whale Transactions"),
    ("dai_transfers", "💠 DAI Transfers"),
    ("dune_api_demo", "🧪 Dune API (connectivity)"),
]


def _fmt_usd(v: Any) -> str:
    """Human $ formatting; '—' when the source value is missing."""
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if abs(f) >= 1_000_000_000:
        return f"${f / 1_000_000_000:.2f}B"
    if abs(f) >= 1_000_000:
        return f"${f / 1_000_000:.2f}M"
    if abs(f) >= 1_000:
        return f"${f / 1_000:.1f}K"
    return f"${f:,.2f}"


def _short_addr(a: Any) -> str:
    if not a or not isinstance(a, str):
        return ""
    if len(a) <= 12:
        return a
    return f"{a[:6]}…{a[-4:]}"


def _row_line(row: dict[str, Any]) -> str:
    """Render one real row into a compact single line (best-effort, honest)."""
    cols = {str(k): v for k, v in row.items()}

    def g(*names: str) -> Any:
        for n in names:
            if n in cols and cols[n] is not None:
                return cols[n]
        return None

    amount = g("amount_usd", "Amount")
    symbol = g("token_pair", "token_pair", "Symbol")
    project = g("project", "project")
    chain = g("blockchain", "blockchain")
    btime = g("block_time", "Block Time", "date_field")
    kind = g("Type", "type")

    bits: list[str] = []
    if kind is not None:
        bits.append(f"{kind}")
    if amount is not None:
        bits.append(_fmt_usd(amount))
    if symbol is not None and str(symbol) not in ("", "SELF"):
        bits.append(str(symbol)[:22])
    if project is not None:
        bits.append(str(project)[:18])
    if chain is not None:
        bits.append(f"on {chain}")
    if btime is not None:
        ts = str(btime).split(".")[0]
        bits.append(ts[:16])

    line = " · ".join(bits)
    if not line.strip():
        line = str(row)[:MAX_ROW_LEN]
    return line[:MAX_ROW_LEN]


async def _fetch_digest() -> dict[str, Any] | None:
    """Backend premium route first, direct Dune provider fallback."""
    try:
        body = await backend_json_get("/api/v1/market/dune", timeout=8.0)
        if body and isinstance(body.get("queries"), dict):
            return body
    except Exception as exc:  # fail-open to direct provider
        logger.debug("backend /dune digest unavailable (%s), using direct provider", exc.__class__.__name__)
    try:
        from app.databus.provider_implementations.dune import dune_onchain_intel

        return await dune_onchain_intel(limit=ROW_LIMIT)
    except Exception as exc:  # never let the command crash
        logger.warning("direct dune digest failed: %s", exc.__class__.__name__)
        return None


async def cmd_dune(update, ctx) -> None:
    """Handle /dune — curated on-chain intel card from real Dune queries."""
    msg = update.message
    if msg is None:
        return

    await msg.chat.send_action("typing")

    digest = await _fetch_digest()
    lines: list[str] = ["🧠 <b>On-Chain Intel</b> <i>(Dune)</i>", ""]

    if digest is None:
        lines.append("❌ Dune intel unavailable right now — try again shortly.")
        text = "\n".join(lines)
        await msg.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)
        return

    queries: dict[str, Any] = digest.get("queries", {})
    for name, title in SECTIONS:
        entry = queries.get(name)
        if not entry or entry.get("error"):
            lines.append(f"{title}: —")
            lines.append("")
            continue

        rows = entry.get("rows") or []
        fetched = (entry.get("fetched_at") or "").split("T")[0]
        lines.append(f"{title} <i>({fetched})</i>")
        if not rows:
            lines.append("  —")
        else:
            for row in rows[:ROW_LIMIT]:
                if isinstance(row, dict):
                    lines.append(f"  • {_row_line(row)}")
        lines.append("")

    lines.extend(
        [
            "<i>Real rows from public Dune queries; '\u2014' = query unavailable.</i>",
            "<i>More intel: /menu</i>",
        ]
    )

    text = "\n".join(lines)
    try:
        await msg.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as exc:  # reply with plain text on parse errors
        logger.warning("dune card HTML reply failed (%s), sending plain", exc.__class__.__name__)
        plain = text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
        await msg.reply_text(plain, disable_web_page_preview=True)
