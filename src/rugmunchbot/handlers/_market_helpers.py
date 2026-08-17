"""Pure formatting / rendering helpers for the market command handlers (Batch 13).

Extracted from ``handlers/market.py``: trending-chain aliases + badge maps,
time-ago / age / liquidity / risk rendering, trending merge/sort/window
logic, the HTML trending list renderer, and the trending keyboard builder.
No network I/O, no database access — only telegram keyboard primitives and
``config`` / ``formatters`` formatting helpers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.domains.telegram.rugmunchbot.config import fmt_number, fmt_pct
from app.domains.telegram.rugmunchbot.formatters import footer_links, sep
from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)


def _time_ago(dt_iso: str | None) -> str:
    """Convert ISO timestamp to human readable relative time."""
    if not dt_iso:
        return ""
    try:
        dt_iso_clean = dt_iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(dt_iso_clean)
        diff = datetime.now(UTC) - dt
        total_secs = diff.total_seconds()
        if total_secs < 0:
            return "just now"
        if total_secs < 60:
            return f"{int(total_secs)}s ago"
        if total_secs < 3600:
            return f"{int(total_secs / 60)}m ago"
        if total_secs < 86400:
            return f"{int(total_secs / 3600)}h ago"
        return f"{diff.days}d ago"
    except (ConnectionError, OSError, TimeoutError):
        return ""


# Chain aliases accepted by /trending (backend RugCharts chain filter)
RUGCHARTS_CHAIN_ALIASES = {
    "sol": "solana",
    "eth": "ethereum",
    "bnb": "bsc",
    "arb": "arbitrum",
    "tron": "tron",
}
TRENDING_CHAINS = "solana · ethereum · base · bsc · arbitrum · tron"

# Chain badge emojis
CHAIN_BADGES = {
    "solana": "🟣",
    "ethereum": "🔵",
    "base": "🔵",
    "bsc": "🟡",
    "arbitrum": "🔵",
    "polygon": "🟣",
    "avalanche": "🔴",
    "optimism": "🔴",
    "tron": "🟢",
    "fantom": "👻",
}

# Age badge thresholds (hours)
AGE_BADGES = [
    (24, "🆕"),  # < 24h
    (168, "🌱"),  # < 7d
    (720, "🌿"),  # < 30d
    (float("inf"), "🌳"),  # > 30d
]

# Liquidity tier thresholds (USD)
LIQ_TIERS = [
    (1_000_000, "💧 Deep"),
    (100_000, "💦 Medium"),
    (10_000, "💧 Shallow"),
    (0, "⚠️ Critical"),
]


def _resolve_chain(arg: str | None) -> str:
    """Normalize a /trending chain argument (aliases → canonical names)."""
    if not arg:
        return "solana"
    chain = arg.lower().strip()
    return RUGCHARTS_CHAIN_ALIASES.get(chain, chain)


def _chain_badge(chain: str) -> str:
    """Return chain badge emoji."""
    return CHAIN_BADGES.get(chain.lower(), "⚪")


def _age_badge(created_at_ms: int | None) -> str:
    """Return age badge based on token creation time."""
    if not created_at_ms:
        return "❓"
    try:
        created = datetime.fromtimestamp(created_at_ms / 1000, tz=UTC)
        age_hours = (datetime.now(UTC) - created).total_seconds() / 3600
        for threshold, badge in AGE_BADGES:
            if age_hours < threshold:
                return badge
    except (ValueError, TypeError, OverflowError, OSError) as e:
        logger.debug("age badge compute failed: %s", e)
    return "❓"


def _liq_badge(liquidity_usd: float) -> str:
    """Return liquidity tier badge."""
    for threshold, badge in LIQ_TIERS:
        if liquidity_usd >= threshold:
            return badge
    return "⚠️ Critical"


def _risk_emoji(risk_score: int | float | None) -> str:
    """Return risk emoji from score."""
    if risk_score is None:
        return "⚪"
    try:
        score = float(risk_score)
        if score >= 70:
            return "🔴"
        if score >= 40:
            return "🟡"
        return "🟢"
    except (TypeError, ValueError) as e:
        logger.debug("risk emoji score parse failed: %s", e)
        return "⚪"


def _normalize_address(addr: str) -> str:
    """Normalize address for deduplication."""
    return addr.lower().strip()


def _token_age_hours(token: dict[str, Any]) -> float | None:
    """Age of a token in hours from ``created_at`` (ms epoch), None when absent."""
    created = token.get("created_at") or token.get("pairCreatedAt")
    if not created:
        return None
    try:
        created_ms = float(created)
    except (TypeError, ValueError):
        return None
    if created_ms <= 0:
        return None
    return max((datetime.now(UTC).timestamp() * 1000 - created_ms) / 3_600_000.0, 0.0)


def _freshness_bonus(token: dict[str, Any]) -> int:
    """Fresh-launch bonus: freshly deployed tokens rank above old infra coins.

    +2 for < 24h old (pump.fun / new launches), +1 for < 7d. Unknown age
    contributes nothing (no invented freshness).
    """
    age_h = _token_age_hours(token)
    if age_h is None:
        return 0
    if age_h < 24:
        return 2
    if age_h < 168:
        return 1
    return 0


def _mover_bonus(token: dict[str, Any]) -> int:
    """Mover bonus: biggest |24h %| movers (up AND down) surface first.

    +2 for |change| >= 100%, +1 for >= 25%. Negative (rug/dump) changes get
    the same bonus as positive ones — degens want the red action too.
    """
    chg = abs(_window_change(token, "24h"))
    if chg >= 100:
        return 2
    if chg >= 25:
        return 1
    return 0


def _merge_trending_sources(
    rugcharts: list[dict[str, Any]] | None,
    dexscreener: list[dict[str, Any]] | None,
    fresh_launches: list[dict[str, Any]] | None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Merge and deduplicate trending tokens — only tokens WITH a real address.

    Degen ranking intent:
      - CoinGecko ``/search/trending`` is NOT merged: it is a CEX search-score
        feed with no contract address, so it would either render as an
        ``[address not verified]`` placeholder or crowd out real meme movers.
      - Fresh launches (``fresh_launches`` — DexScreener token-profiles /
        pump.fun new deploys) and RugCharts carry the highest priority.
      - Tokens without an ``address`` are dropped — never emitted without a
        real contract address.
      - Ranking rewards: source priority + freshness (newly deployed) + mover
        magnitude (|24h %|, both directions) + live volume/liquidity.
    """
    merged: dict[str, dict[str, Any]] = {}

    # Priority: RugCharts (full data) = fresh launches (degen gold) > DexScreener boosts
    for source_tokens, priority in [
        (rugcharts or [], 3),
        (fresh_launches or [], 3),
        (dexscreener or [], 2),
    ]:
        for t in source_tokens:
            addr = (t.get("address") or "").strip()
            if not addr:
                continue  # no real contract address → never surface (no placeholders)
            norm = _normalize_address(addr)
            existing = merged.get(norm)

            # Quality score: has_name (2) + has_symbol (1) + has_volume (1) + has_liquidity (1) + priority (0-3)
            name = (t.get("name") or "").strip()
            symbol = (t.get("symbol") or "").strip()
            has_name = bool(name and name != "Unknown")
            has_symbol = bool(symbol and symbol != "???")
            has_volume = float(t.get("volume_24h") or 0) > 0
            has_liquidity = float(t.get("liquidity_usd") or 0) > 0
            quality = (
                (2 if has_name else 0)
                + (1 if has_symbol else 0)
                + (1 if has_volume else 0)
                + (1 if has_liquidity else 0)
                + priority
            )

            existing_quality = 0
            if existing:
                ename = (existing.get("name") or "").strip()
                esymbol = (existing.get("symbol") or "").strip()
                existing_quality = (
                    (2 if ename and ename != "Unknown" else 0)
                    + (1 if esymbol and esymbol != "???" else 0)
                    + (1 if float(existing.get("volume_24h") or 0) > 0 else 0)
                    + (1 if float(existing.get("liquidity_usd") or 0) > 0 else 0)
                    + existing.get("_priority", 0)
                )

            if not existing or quality > existing_quality:
                merged[norm] = {**t, "_priority": priority, "_quality": quality, "_source": t.get("source", "unknown")}

    # Convert to list and sort by: quality + freshness + mover magnitude, then
    # |24h change| (both directions), then volume.
    def _rank_key(x: dict[str, Any]) -> tuple[float, float, float]:
        return (
            float(x.get("_quality", 0)) + _freshness_bonus(x) + _mover_bonus(x),
            abs(_window_change(x, "24h")),
            float(x.get("volume_24h") or 0),
        )

    result = list(merged.values())
    result.sort(key=_rank_key, reverse=True)

    # Filter out tokens with "Unknown" names (prefer named tokens from DexScreener)
    result = [t for t in result if (t.get("name") or "").strip() and (t.get("name") or "").strip() != "Unknown"]

    return result[:limit]


def _clean_token_name(name: str, symbol: str) -> tuple[str, str]:
    """Clean up token names from DexScreener/CoinGecko/RugCharts."""
    import re

    if not name or name == "Unknown":
        return symbol or "Unknown", symbol or "???"

    # Filter out garbage patterns (meme descriptions, etc.)
    garbage_patterns = [
        r"on robinhood.*",
        r"crawled out of.*",
        r"just a smol.*",
        r"dev leave.*",
        r"community took over.*",
        r"sent to a.*",
        r"smolsem.*",
        r"anaem.*",
        r"the true origin.*",
        r"built her empire.*",
        r"the soft power.*",
        r"born in \d{4}.*",
        r"slightly sassy.*",
    ]
    cleaned = name
    for pattern in garbage_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

    # If name is too long or looks like a sentence, use symbol
    if len(cleaned) > 40 or cleaned.count(" ") > 5:
        return symbol or "Unknown", symbol or "???"

    # If name is empty after cleaning, use symbol
    if not cleaned:
        return symbol or "Unknown", symbol or "???"

    # If symbol is in name, prefer name
    if symbol and symbol.upper() in cleaned.upper():
        return cleaned[:40], symbol

    return cleaned[:40], symbol or "???"


def _trending_display(token: dict[str, Any]) -> tuple[str, str]:
    """Return a display-safe (name, symbol), trimming long/garbage values."""
    raw_name = (token.get("name") or "").strip()
    symbol = (token.get("symbol") or "").strip()
    # If name looks like an address (starts with 0x or is 44 chars), use symbol
    if raw_name.startswith("0x") or len(raw_name) == 44:
        raw_name = ""
    return _clean_token_name(raw_name, symbol)


# ── Trending time windows ──────────────────────────────────────────────
TRENDING_WINDOWS = ("1h", "24h", "7d")
_WINDOW_FIELD = {"1h": "change_1h", "24h": "change_24h", "7d": "change_7d"}


def _window_change(token: dict[str, Any], window: str) -> float:
    """Change % for the requested window, falling back to 24h when absent."""
    key = _WINDOW_FIELD.get(window, "change_24h")
    val = token.get(key)
    if val is None:
        val = token.get("change_24h", 0)
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


def _sort_trending(tokens: list[dict[str, Any]], window: str) -> list[dict[str, Any]]:
    """Rank trending tokens for the requested window (mutation-free).

    Degen ranking: rank by |change| for the window so the biggest movers in
    BOTH directions (green pumps AND red rugs) surface — a -90% dump is
    exactly as interesting to a degen as a +500% pump. The 1h window has no
    reliable hourly deltas from our sources, so it ranks by 24h volume.
    """
    if window == "1h":
        return sorted(tokens, key=lambda t: float(t.get("volume_24h") or 0), reverse=True)
    if window == "7d" and any(t.get("change_7d") is not None for t in tokens):
        return sorted(tokens, key=lambda t: abs(float(t.get("change_7d") or 0)), reverse=True)
    return sorted(tokens, key=lambda t: abs(float(t.get("change_24h") or 0)), reverse=True)


def _window_note(window: str, tokens: list[dict[str, Any]]) -> str:
    """Honest footnote when the window is backed by fallback data."""
    if window == "1h":
        return "🕒 1h window — ranked by volume (hourly deltas pending)"
    if window == "7d" and not any(t.get("change_7d") is not None for t in tokens):
        return "📅 7d window — using 24h deltas (7d series pending)"
    return ""


def _render_trending_text(tokens: list[dict[str, Any]], chain: str, window: str) -> str:
    """HTML trending list with badges, clickable CAs, risk indicators."""
    tokens = _sort_trending(tokens, window)
    lines = [f"🔥 <b>Trending — {chain.capitalize()} · {window}</b>", sep()]

    rendered = 0
    for t in tokens:
        addr = t.get("address", "")
        if not addr:
            continue
        name, symbol = _trending_display(t)
        rendered += 1
        if rendered > 8:
            break
        chg = _window_change(t, window)
        vol = float(t.get("volume_24h") or 0)
        liq = float(t.get("liquidity_usd") or 0)
        risk = t.get("risk_score")
        created = t.get("created_at")

        # Badges
        chain_badge = _chain_badge(chain)
        age_badge = _age_badge(created)
        liq_badge = _liq_badge(liq)
        risk_emoji = _risk_emoji(risk)
        chg_emoji = "🟢" if chg >= 0 else "🔴"

        # Bundle warning
        bundle_flag = " 🚨" if t.get("bundle_detected") else ""

        # If name unknown, show truncated address instead
        display_name = name if name not in ("Unknown", "???", "") else f"{addr[:8]}...{addr[-6:]}"
        display_symbol = symbol if symbol not in ("Unknown", "???", "") else "?"
        lines.append(
            f"{rendered}. {chain_badge} {age_badge} {risk_emoji} <b>{display_name}</b> ({display_symbol}){bundle_flag}\n"
            f"   {liq_badge} | {chg_emoji} {fmt_pct(chg)} | Vol: {fmt_number(vol)}\n"
            f"   <code>{addr}</code>"
        )

    note = _window_note(window, tokens)
    if note:
        lines.append("")
        lines.append(f"<i>{note}</i>")
    lines.append(f"\n<i>Chains: {TRENDING_CHAINS}</i>")
    lines.append(footer_links())
    return "\n".join(lines)


def _build_trending_kb(tokens: list[dict[str, Any]], chain: str, window: str) -> InlineKeyboardMarkup:
    """Trending keyboard: named token buttons + chain/window selectors.

    Design: each token gets ONE button with its name → opens scan.
    Chain row switches chain, window row changes time frame.
    Clean, scannable, no button overload.
    """
    rows: list[list[InlineKeyboardButton]] = []

    # Token buttons — one per token, named, opens scan
    for t in tokens[:8]:
        addr = t.get("address", "")
        if not addr:
            continue
        name, symbol = _trending_display(t)
        # If name/symbol are unknown, use truncated address
        if name in ("Unknown", "???", "") or symbol in ("Unknown", "???", ""):
            label = f"🔍 {addr[:8]}...{addr[-6:]}"[:40]
        else:
            label = f"🔍 {name} ({symbol})"[:40]
        rows.append([InlineKeyboardButton(label, callback_data=f"scan_{addr}_{chain}")])

    # Chain selector row (2 per row for compact layout)
    chain_list = ["solana", "ethereum", "base", "bsc", "arbitrum"]
    chain_emoji = {"solana": "🟣", "ethereum": "🔵", "base": "🔵", "bsc": "🟡", "arbitrum": "🔵"}
    chain_row = []
    for c in chain_list:
        emoji = chain_emoji.get(c, "⚪")
        label = f"{emoji} {c.capitalize()}"
        if len(chain_row) >= 3:
            rows.append(chain_row)
            chain_row = []
        chain_row.append(
            InlineKeyboardButton(f"[{label}]" if c == chain else label, callback_data=f"trending_chain_{c}")
        )
    if chain_row:
        rows.append(chain_row)

    # Window selector row
    win_row = [
        InlineKeyboardButton(f"[{w}]" if w == window else w, callback_data=f"trending_win_{chain}_{w}")
        for w in TRENDING_WINDOWS
    ]
    rows.append(win_row)

    # Refresh + Menu
    rows.append(
        [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"trending_refresh_{chain}"),
            InlineKeyboardButton("🏠 Menu", callback_data="menu_main"),
        ]
    )
    return InlineKeyboardMarkup(rows)
