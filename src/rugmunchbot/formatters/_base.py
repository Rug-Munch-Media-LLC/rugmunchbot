"""Core formatting & utility helpers for the RugMunch bot.

Extracted from ``formatters.py`` during the Batch 11 de-godding pass:
address detection, text/link helpers, welcome/paywall text and the
explainable-scoring + funding-analysis blocks. ``formatters.py`` re-exports
every public name; imports elsewhere are unchanged.
"""

from __future__ import annotations

import os
import re
from typing import Any

from telegram import InlineKeyboardButton

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    CHAIN_IDS,
    DEX_REF_LINKS,
    TELEGRAM_CHANNEL,
    TIERS,
    WEB_APP_URL,
)

# ── FREE PUBLIC BETA (2026-08) ──────────────────────────────────────────
# rugmunchbot is in FREE PUBLIC BETA: every premium feature is unlocked for
# everyone while we test. Billing surfaces stay functional (they are the
# future revenue path) but beta status must be prominent. Remove this block
# and the free-tier quota bump in config.py when beta ends.
# The optional Stars deep-scan pack is the ONE paid surface during beta —
# copy must state that so "everything is free" never contradicts the
# 250★ Deep Scan buttons.
BETA_BANNER = (
    "\U0001f680 <b>FREE PUBLIC BETA</b> \u2014 all premium features "
    "unlocked while we test.\n"
    "\U0001f4c5 <b>Premium plans coming soon</b> \u2014 beta users get "
    "launch discounts.\n"
    "\u2b50 Optional: \U0001f52c <b>Deep Scan</b> buttons run on Telegram "
    "Stars (250\u2605); everything else is free."
)
BETA_STATUS_LINE = "\U0001f680 <b>FREE PUBLIC BETA</b> \u2014 all features unlocked \U0001f389"
BETA_PAYMENTS_LINE = (
    "\U0001f680 <b>In beta</b> \u2014 every feature is free; optional "
    "Telegram Stars purchases (deep scans, top-ups) are live for early supporters."
)

# --- Address detection helpers ---
TOKEN_RE = re.compile(r"\$([A-Za-z]{2,10})\b")
EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def is_evm(addr: str) -> bool:
    from app.core.shared_utils import is_evm_address

    return is_evm_address(addr)


def is_sol(addr: str) -> bool:
    from app.core.shared_utils import is_solana_address

    return is_solana_address(addr)


def detect_chain(addr: str) -> str:
    if is_evm(addr):
        return "ethereum"
    if is_sol(addr):
        return "solana"
    return "unknown"


def short_addr(addr: str) -> str:
    if len(addr) > 12:
        return f"{addr[:6]}...{addr[-4:]}"
    return addr


# --- Text helpers ---
def sep(char: str = "\u2501", length: int = 28) -> str:
    return char * length


def thin_sep(char: str = "\u2500", length: int = 24) -> str:
    return char * length


# --- Referral codes (single source of truth for trade links) --------------
# Owner referral codes appended to the trade-link URLs built in this module
# (``gmgn_url`` / ``dextools_url`` → ``footer_links`` / ``dex_buttons``).
# Empty string = the ``?ref=`` param is omitted entirely, so links stay
# clean until a code is set. Environment overrides win over the dict:
# ``RMI_REF_GMGN`` / ``RMI_REF_DEXTOOLS``.
REF_CODES = {
    # GMGN already ships with `ref=rugmunch` in config.DEX_REF_LINKS — keep
    # that attribution here so the footer GMGN link matches the keyboard.
    # Change to "" (or set RMI_REF_GMGN) to drop the param.
    "gmgn": "rugmunch",
    # fill in your ref code
    "dextools": "",
}
# DexScreener has no referral program → intentionally absent from REF_CODES
# (``dexscreener_url`` never appends a ``?ref=`` param).


def _ref_code(platform: str) -> str:
    """Resolve the owner's referral code for a platform.

    Env overrides (``RMI_REF_GMGN`` / ``RMI_REF_DEXTOOLS``) take precedence
    over ``REF_CODES``; an empty result omits the ``?ref=`` param.
    """
    env_name = f"RMI_REF_{platform.upper()}"
    return os.environ.get(env_name, REF_CODES.get(platform, ""))


def footer_links(address: str = "", chain: str = "") -> str:
    """Bot footer with links. When a token is provided, includes
    GMGN → DexScreener → DexTools → RugCharts → RugMaps as a clean SINGLE
    tappable-text line (GMGN carries the owner's ref code) so the report
    ends with one links footer instead of a button stack. HTML-safe for
    Telegram (only ``<a>`` tags)."""
    if address:
        trade = (
            f'<a href="{gmgn_url(address, chain)}">GMGN</a>'
            f' | <a href="{dexscreener_url(address, chain)}">DexScreener</a>'
            f' | <a href="{dextools_url(address, chain)}">DexTools</a>'
        )
        charts = (
            f'\U0001f4c8 <a href="https://rugmunch.io/rugcharts?token={address}&chain={chain}">RugCharts</a>'
            f' | \U0001f5fa\ufe0f <a href="https://rugmunch.io/rugmaps?token={address}&chain={chain}">RugMaps</a>'
        )
        return (
            f"\n{sep()}\n{trade} | {charts}\n"
            f'\U0001f50d Rug Munch Bot | <a href="{TELEGRAM_CHANNEL}">/help</a> | @RugMunchBot'
        )
    return f'\n{sep()}\n\U0001f50d Rug Munch Bot | <a href="{TELEGRAM_CHANNEL}">/help</a> | @RugMunchBot'


def web_scan_button(address: str, chain: str = "") -> InlineKeyboardButton:
    url = f"{WEB_APP_URL}?token={address}"
    if chain:
        url += f"&chain={chain}"
    return InlineKeyboardButton("\U0001f310 Full Report on RugMunch.io", url=url)


# DexScreener chain slugs (match ``chainId`` values from DexScreener pairs).
DEXSCREENER_CHAINS = {
    "solana": "solana",
    "ethereum": "ethereum",
    "base": "base",
    "bsc": "bsc",
    "arbitrum": "arbitrum",
    "tron": "tron",
    "polygon": "polygon",
    "avalanche": "avax",
    "optimism": "optimism",
}


def dexscreener_url(address: str, chain: str) -> str:
    """DexScreener token page URL — tap-to-open chart for any token."""
    slug = DEXSCREENER_CHAINS.get((chain or "").lower(), (chain or "solana").lower())
    return f"https://dexscreener.com/{slug}/{address}"


# GMGN chain slugs (match the path segment GMGN uses per chain).
GMGN_CHAINS = {
    "solana": "sol",
    "ethereum": "eth",
    "base": "base",
    "bsc": "bsc",
    "polygon": "polygon",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "avalanche": "avax",
    "fantom": "fantom",
    "tron": "tron",
    "robinhood": "robinhood",
}


def gmgn_url(address: str, chain: str) -> str:
    """GMGN token page URL — smart-money tracking + trading terminal.

    Appends the owner's ``?ref=`` param when a GMGN ref code is configured
    (``REF_CODES["gmgn"]`` or ``RMI_REF_GMGN``); omits it when empty.
    """
    slug = GMGN_CHAINS.get((chain or "").lower(), (chain or "solana").lower())
    url = f"https://gmgn.ai/{slug}/token/{address}"
    ref = _ref_code("gmgn")
    if ref:
        url += f"?ref={ref}"
    return url


# DexTools chain slugs (match the path segment DexTools uses per chain).
DEXTOOLS_CHAINS = {
    "solana": "sol",
    "ethereum": "ether",
    "base": "base",
    "bsc": "bnb",
    "arbitrum": "arbitrum",
    "polygon": "polygon",
    "avalanche": "avalanche",
    "optimism": "optimism",
    "tron": "tron",
}


def dextools_url(address: str, chain: str) -> str:
    """DexTools pair page URL — chart explorer for any token.

    Appends the owner's ``?ref=`` param when a DexTools ref code is
    configured (``REF_CODES["dextools"]`` or ``RMI_REF_DEXTOOLS``); omits
    it when empty.
    """
    slug = DEXTOOLS_CHAINS.get((chain or "").lower(), (chain or "solana").lower())
    url = f"https://www.dextools.io/app/en/{slug}/pair-explorer/{address}"
    ref = _ref_code("dextools")
    if ref:
        url += f"?ref={ref}"
    return url


def explorer_url(address: str, chain: str) -> str:
    """Block explorer URL for a token/wallet on the given chain."""
    chain_l = (chain or "").lower()
    if chain_l == "solana":
        return f"https://solscan.io/account/{address}"
    domains = {
        "ethereum": "etherscan.io",
        "bsc": "bscscan.com",
        "polygon": "polygonscan.com",
        "arbitrum": "arbiscan.io",
        "optimism": "optimistic.etherscan.io",
        "base": "basescan.org",
        "avalanche": "snowtrace.io",
        "fantom": "ftmscan.com",
        "tron": "tronscan.org",
    }
    domain = domains.get(chain_l, "etherscan.io")
    return f"https://{domain}/address/{address}"


# ── DEX trade-button ordering ─────────────────────────────────────────────
# Highest-value platforms first so the first inline-keyboard row leads with
# the biggest revenue/utility (GMGN smart-money terminal, Jupiter swap).
# DEX_REF_LINKS stays the source of truth for what ships; this tuple only
# sets precedence among chain-eligible platforms (unlisted → sorted last).
DEX_BUTTON_PRIORITY = (
    "gmgn",
    "jupiter",
    "photon",
    "banana",
    "odinbot",
    "axiom",
    "padre",
    "birdeye",
    "oneinch",
    "pancakeswap",
)
DEX_BUTTONS_PER_ROW = 2
# Chain-agnostic fallback for empty/unknown chains: Telegram trading bots
# whose start links embed our ref codes and never depend on the token
# address. This guarantees a safe minimal set — we never build a token-page
# URL against the wrong chain.
UNKNOWN_CHAIN_MINIMAL = ("odinbot", "banana")


def _ref_platform_buttons(token: str, chain: str, only: tuple[str, ...] | None = None) -> list[InlineKeyboardButton]:
    """Chain-filtered ``DEX_REF_LINKS`` buttons in ``DEX_BUTTON_PRIORITY`` order.

    Fills ``{token}`` / ``{chain}`` / ``{chain_id}`` placeholders from the
    scan's address + chain so every link lands on the token with our ref
    embedded. ``only`` restricts to a fixed platform subset (used for the
    unknown-chain minimal set); ``None`` filters by ``dex["chains"]``.
    """
    chain_l = (chain or "").lower()
    ordered = sorted(
        DEX_REF_LINKS.items(),
        key=lambda kv: DEX_BUTTON_PRIORITY.index(kv[0]) if kv[0] in DEX_BUTTON_PRIORITY else len(DEX_BUTTON_PRIORITY),
    )
    buttons: list[InlineKeyboardButton] = []
    for key, dex in ordered:
        if only is not None:
            if key not in only:
                continue
        elif chain_l not in dex.get("chains", []):
            continue
        url = dex["url"].format(
            token=token,
            chain=chain_l,
            chain_id=CHAIN_IDS.get(chain_l, 1),
        )
        buttons.append(InlineKeyboardButton(f"{dex['emoji']} {dex['name']}", url=url))
    return buttons


def dex_button_rows(token: str, chain: str, per_row: int = DEX_BUTTONS_PER_ROW) -> list[list[InlineKeyboardButton]]:
    """Chain-filtered referral trade buttons grouped into rows of ≤ ``per_row``.

    Renders the full ``DEX_REF_LINKS`` set for the chain in
    ``DEX_BUTTON_PRIORITY`` order (GMGN/Jupiter lead), with DexScreener +
    DexTools chart buttons appended after the referral platforms. Every
    token-embedding platform (GMGN/Photon/Birdeye charts, Jupiter/1inch/
    PancakeSwap swaps) gets the scan's address filled in — see
    ``_ref_platform_buttons``. Telegram signup bots (OdinBot/Banana Gun)
    and home-only terminals (Axiom/Padre) carry no per-token URL by design
    (documented in ``config.DEX_REF_LINKS``). An
    empty/unknown chain returns a safe minimal set — the Telegram trading
    bots (OdinBot / Banana Gun) whose ref-coded start links never depend on
    the token address — so no token-page URL is ever built for the wrong
    chain. Pure: no network, no DB.
    """
    chain_l = (chain or "").lower()
    if chain_l in ("", "unknown"):
        buttons = _ref_platform_buttons(token, chain_l, only=UNKNOWN_CHAIN_MINIMAL)
    else:
        buttons = _ref_platform_buttons(token, chain_l)
        # Chart trade links — DexScreener (no ref program) + DexTools (ref via
        # REF_CODES / RMI_REF_DEXTOOLS when configured). GMGN already ships via
        # DEX_REF_LINKS above, so it is not duplicated here.
        buttons.append(InlineKeyboardButton("\U0001f986 DexScreener", url=dexscreener_url(token, chain_l)))
        buttons.append(InlineKeyboardButton("\U0001f4c8 DexTools", url=dextools_url(token, chain_l)))
    per_row = max(1, per_row)
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


def dex_buttons(token: str, chain: str) -> list[InlineKeyboardButton]:
    """Flattened referral trade buttons (back-compat) — see ``dex_button_rows``.

    Returns the same chain-filtered full set in priority order as a flat
    list so existing callers (``keyboards.scan_result_kb`` chunking 2/row)
    keep working unchanged.
    """
    return [button for row in dex_button_rows(token, chain) for button in row]


def social_proof_text() -> str:
    """Evidence-first "why trust us" block.

    Only figures verified in the fleet docs: 17 chains / 359M+ on-chain
    rows (lite-chain-indexer README) and 200+ tools (rmi-backend
    ARCHITECTURE.md). No invented user/scan counters and no DB access, so
    this stays a pure, deterministic string formatter.
    """
    return (
        "\U0001f3d7\ufe0f <b>Why trust us</b>\n"
        "\U0001f517 17 chains indexed \u00b7 359M+ on-chain rows\n"
        "\U0001f6e0\ufe0f 200+ tools \u00b7 24/7 AI \u2014 free during public beta"
    )


def about_text() -> str:
    """Backstory block — who is behind Rug Munch Bot.

    Used by /start, /help and /faq so every surface carries the same
    company line. HTML-safe for Telegram: only ``<b>`` tags.
    """
    return (
        "\U0001f916 <b>About</b>\n"
        "Rug Munch Bot is created by the team behind "
        "<b>RMI (Rug Munch Intelligence)</b> \u2014 a division of "
        "<b>Rug Munch Media LLC</b>, a Wyoming-based company \u2014 to "
        "protect retail investors from crypto scams with on-chain "
        "intelligence, scanner forensics, and AI analysis.\n\n"
        "Developed by @cryptorugmunch and the team at Rug Munch Media LLC."
    )


def faq_text() -> str:
    """Lightweight FAQ — plain-language answers to common questions.

    Pure string formatter (no network, no DB) so /faq always renders and is
    unit-testable. HTML-safe for Telegram: only ``<b>`` / ``<code>`` tags and
    ``&amp;`` escaping — no raw markdown.
    """
    return (
        "\U0001f4d6 <b>Rug Munch Bot - FAQ</b>\n\n"
        "\U0001f916 <b>What is Rug Munch Bot?</b>\n"
        "An AI scam-detection copilot: paste any token address or wallet and "
        "get instant audits, forensics and risk scores.\n\n"
        "\U0001f50d <b>What can you scan?</b>\n"
        "Tokens on EVM chains and Solana, wallets, deployers, LP health, "
        "honeypots, MEV risk, hype scores and more. Try /scan "
        "<code>address</code> or /wallet <code>address</code>.\n\n"
        "\U0001f4b0 <b>Is it free?</b>\n"
        "Yes! We\u2019re in <b>FREE PUBLIC BETA</b> \u2014 every premium "
        "feature is unlocked while we test. Paid plans (Scout/Hunter/Pro) "
        "come later.\n\n"
        "\U0001f3db\ufe0f <b>What is RMI?</b>\n"
        "RMI (Rug Munch Intelligence) is the team behind the bot, owned by "
        "<b>Rug Munch Media LLC</b>, a Wyoming-based company. We build "
        "on-chain intelligence tools that protect retail investors.\n\n"
        "\U0001f3af <b>How accurate are the scans?</b>\n"
        "Our AEGIS engine runs 9 forensics modules over 17 indexed chains "
        "(359M+ on-chain rows) with 200+ tools. No tool is 100% \u2014 "
        "always DYOR, and pair a scan with the charts on rugmunch.io.\n\n"
        "\u26a0\ufe0f <b>See a scam?</b>\n"
        "Report it with /report <code>address</code> \u2014 it keeps "
        "everyone safer.\n\n"
        "\U0001f4d6 More: /help for the full command guide"
    )


def welcome_card_text() -> str:
    """Modern capability overview — brand line, "what I can do" list, CTA.

    Shown by /start, the main menu (``menu_main``) and the welcome card.
    Every capability bullet maps to real bot commands (see ``cmd_help``
    for the full reference). HTML-safe for Telegram: only ``<b>`` /
    ``<i>`` / ``<code>`` tags and ``&amp;`` escaping — no raw markdown.
    """
    return (
        f"\U0001f6e1\ufe0f <b>Rug Munch Bot</b> \u2014 by @cryptorugmunch and "
        f"the team at Rug Munch Media LLC\n"
        f"<i>The scam-detection copilot for crypto</i>\n\n"
        f"{social_proof_text()}\n\n"
        f"<b>What I can do</b>\n"
        f"\U0001f50d <b>Scan</b> \u2014 instant token audits &amp; AEGIS forensics\n"
        f"\U0001f45b <b>Wallet</b> \u2014 forensics, portfolio &amp; funding analysis\n"
        f"\U0001f575\ufe0f <b>Trace</b> \u2014 cross-chain wallet tracing (Arkham-style)\n"
        f"\U0001f4b0 <b>Price</b> \u2014 live prices, charts &amp; market snapshots\n"
        f"\U0001f4ca <b>Technical</b> \u2014 RSI, MACD, support &amp; resistance\n"
        f"\U0001f4f0 <b>News</b> \u2014 live crypto news + RAG AI chat\n"
        f"\U0001f514 <b>Alerts</b> \u2014 price, watchlist &amp; whale alerts\n"
        f"\U0001f4da <b>Scam School</b> \u2014 learn the plays &amp; KOL/scammer tracking\n\n"
        f"{about_text()}\n\n"
        f"{BETA_BANNER}\n\n"
        f"\U0001f4cc Try <code>/scan</code> <code>address</code> or "
        f"<code>/chat</code> <code>question</code>\n"
        f"\U0001f4cc New here? <code>/faq</code> \u2014 how it works\n"
        f"\U0001f4cc Pick a section below \U0001f447"
        f"{footer_links()}"
    )


def paywall_text(user_id: int, action: str = "scan") -> str:
    tier = db.get_user_tier(user_id)
    scans, ai = db.get_weekly_usage(user_id)
    user = db.get_or_create_user(user_id)
    tc = TIERS.get(tier, TIERS["free"])
    limit = tc.get("scans_per_week", 25) if action == "scan" else tc.get("ai_msgs_per_week", 15)
    used = scans if action == "scan" else ai
    bonus = user.get("bonus_scans", 0) if action == "scan" else user.get("bonus_ai_msgs", 0)
    # Public beta: hitting the (boosted) weekly quota is a REAL limit, but
    # tell free users it's a beta allocation that resets — not a paywall.
    beta_note = ""
    if tier == "free":
        beta_note = (
            "\n\U0001f680 <b>Beta:</b> your weekly limit is boosted for the "
            "public beta \u2014 resets Monday 00:00 UTC.\n"
        )
    return (
        f"\u26a1 <b>Weekly {action.title()} Limit Reached</b>\n\n"
        f"\U0001f4ca <b>{tc['name']} Tier:</b> {used}/{limit} used this week\n"
        f"\U0001f381 <b>Bonus Balance:</b> {bonus} extra\n"
        f"{beta_note}"
        f"\n<b>Unlock more power:</b>\n\n"
        f"\U0001f6f0\ufe0f <b>Monitor (250\u2605/30d)</b> \u2014 live LP/whale + tax/ownership/mint change alerts\n"
        f"\U0001f52c <b>Deep Scan (250\u2605)</b> \u2014 full forensics on demand\n"
        f"\U0001f4b0 <b>Core /scan + chat stay free.</b>\n\n"
        f"Or buy one-time top-ups - <b>never expire!</b>\n"
        f"\U0001f50b 10 scans = \u2b5075 | 50 scans = \u2b50300 | 100 scans = \u2b50500"
    )


# --- Risk helpers ---
# Single source of truth for risk colors / verdicts. Every risk renderer in
# this module (and only this module) maps through these constants so the
# color language stays consistent: 🔴 high / 🟡 medium / 🟢 low / ⚪ unknown.
RISK_EMOJI = {"high": "\U0001f534", "medium": "\U0001f7e1", "low": "\U0001f7e2"}
RISK_VERDICT = {"high": "HIGH RISK", "medium": "CAUTION", "low": "LOW RISK"}
RISK_UNKNOWN_EMOJI = "\u26aa"
RISK_HIGH_SCORE = 70.0
RISK_MID_SCORE = 40.0
RISK_BAR_FILLED = "\u2b1b"
RISK_BAR_EMPTY = "\u2b1c"


def _risk_level(score: float) -> str:
    """Map a 0-100 risk score to a level key (high/medium/low)."""
    if score >= RISK_HIGH_SCORE:
        return "high"
    if score >= RISK_MID_SCORE:
        return "medium"
    return "low"


def _risk_indicator(score: float) -> str:
    """Color-coded risk emoji for a 0-100 score (🔴 / 🟡 / 🟢)."""
    return RISK_EMOJI[_risk_level(score)]


def _pct_emoji(value: float, *, high: float, mid: float) -> str:
    """Map a percentage to a risk emoji using per-metric thresholds.

    Thresholds are strict (``value > high`` is red) so boundary behavior
    matches the original funding-analysis rendering exactly.
    """
    if value > high:
        return RISK_EMOJI["high"]
    if value > mid:
        return RISK_EMOJI["medium"]
    return RISK_EMOJI["low"]


def _verdict_sentence(r: dict[str, Any]) -> str:
    """Pick the single most important finding for the verdict headline.

    Only reads keys that actually exist in ``r`` — absent keys are skipped
    (never treated as ``False``), so a missing field cannot produce a false
    claim. Priority: honeypot > mintable > ownership > blacklist > unverified
    > liquidity; otherwise a clean bill.
    """
    if r.get("honeypot") is True:
        return "Honeypot detected \u2014 selling may be blocked; extreme caution advised."
    if r.get("mintable") is True:
        return "Supply is mintable \u2014 the owner can print unlimited new tokens."
    if r.get("owner_renounced") is False:
        return "Ownership is not renounced \u2014 the deployer retains contract control."
    if r.get("can_blacklist") is True:
        return "Owner can blacklist addresses \u2014 holders can be frozen out."
    if r.get("contract_verified") is False:
        return "Contract source is not verified \u2014 code cannot be audited."
    liquidity = r.get("liquidity_usd") or r.get("liquidity")
    try:
        liq_float = float(liquidity) if liquidity is not None else None
    except (TypeError, ValueError):
        liq_float = None
    if liq_float is not None and liq_float < 10_000:
        return "Liquidity is thin \u2014 large price swings and exit issues possible."
    return "No critical flags \u2014 normal caution advised."


def format_verdict_header(r: dict[str, Any]) -> str:
    """Big color-coded verdict headline for a scan report.

    Renders a large emoji + one-word verdict + score line (e.g.
    ``🔴 <b>HIGH RISK</b> — Score 82/100``) followed by a one-line
    plain-English sentence naming the single most important finding.
    A missing/unparseable ``risk_score`` renders a neutral UNKNOWN line
    instead of a misleading 0/100 — this function never fabricates data.
    """
    raw = r.get("risk_score")
    try:
        score = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        score = None
    if score is None:
        headline = f"{RISK_UNKNOWN_EMOJI} <b>UNKNOWN</b> \u2014 no risk score available"
    else:
        level = _risk_level(score)
        headline = f"{RISK_EMOJI[level]} <b>{RISK_VERDICT[level]}</b> \u2014 Score {score:.0f}/100"
    return f"{headline}\n{_verdict_sentence(r)}"


def _price_diff_pct(p1: float, p2: float) -> str:
    if p1 <= 0 or p2 <= 0:
        return "N/A"
    diff = ((p1 - p2) / max(p1, p2)) * 100
    abs_diff = abs(diff)
    if abs_diff < 0.01:
        return "\u2248 same"
    cheaper = "T1" if diff < 0 else "T2"
    return f"{cheaper} {abs_diff:.2f}% cheaper"


# --- Explainable scoring breakdown ---
DIMENSION_LABELS = {
    "security": "\U0001f512 Contract Security",
    "liquidity": "\U0001f4a7 Liquidity",
    "holders": "\U0001f465 Holders",
    "deployer": "\U0001f468\u200d\U0001f4bb Deployer",
    "market": "\U0001f4ca Market",
}


def format_scoring_breakdown(breakdown: dict[str, Any]) -> str:
    """Render the v2 explainable per-dimension scoring breakdown.

    Each dimension carries score/max/delta/flags. Output is a compact
    table with a color-coded risk bar (🔴 high / 🟡 medium / 🟢 low per
    dimension) so users can see WHY a token scored the way it did at a
    glance. Delta arrows (▲/▼) show the change direction when present.
    """
    if not breakdown:
        return ""
    lines = ["", "\U0001f9e9 <b>Scoring Breakdown</b>", thin_sep()]
    bar_len = 10
    for dim, label in DIMENSION_LABELS.items():
        entry = breakdown.get(dim)
        if not isinstance(entry, dict):
            continue
        score = float(entry.get("score", 0) or 0)
        maximum = float(entry.get("max", 0) or 0)
        delta = float(entry.get("delta", 0) or 0)
        filled = round((score / maximum) * bar_len) if maximum else 0
        filled = max(0, min(filled, bar_len))
        bar = RISK_BAR_FILLED * filled + RISK_BAR_EMPTY * (bar_len - filled)
        ratio = (score / maximum) if maximum else 0.0
        marker = RISK_EMOJI[_risk_level(ratio * 100)]
        delta_str = ""
        if delta > 0:
            delta_str = " \u25b2"
        elif delta < 0:
            delta_str = " \u25bc"
        lines.append(f"  {marker} {label:<22} <code>{bar}</code> <b>{score:.0f}/{maximum:.0f}</b>{delta_str}")
        flags = entry.get("flags") or []
        if flags:
            for fl in flags[:2]:
                lines.append(f"      \u26a0\ufe0f {fl}")
    return "\n".join(lines)


def format_funding_analysis(r: dict[str, Any]) -> str:
    """Render the funding analysis block (exchange-funded / fresh / scam overlap).

    Reads the convenience keys populated by the scanner layer:
    ``exchange_funded_pct``, ``fresh_wallet_pct``, ``holder_overlap_pct``.
    Missing values render as ⚪ N/A so the section is stable across v1/v2.
    Colors map through ``RISK_EMOJI`` with per-metric thresholds
    (exchange 50/30, fresh 40/20, overlap 30/10).
    """
    exchange = r.get("exchange_funded_pct")
    fresh = r.get("fresh_wallet_pct")
    overlap = r.get("holder_overlap_pct")
    if exchange is None and fresh is None and overlap is None:
        return ""
    lines = ["", "\U0001f4b8 <b>Funding Analysis</b>", thin_sep()]
    if exchange is not None:
        emoji = _pct_emoji(exchange, high=50, mid=30)
        lines.append(f"  {emoji} Exchange-Funded:        <b>{exchange:.0f}%</b>")
    else:
        lines.append(f"  {RISK_UNKNOWN_EMOJI} Exchange-Funded:        <b>N/A</b>")
    if fresh is not None:
        emoji = _pct_emoji(fresh, high=40, mid=20)
        lines.append(f"  {emoji} Fresh Wallets:           <b>{fresh:.0f}%</b>")
    else:
        lines.append(f"  {RISK_UNKNOWN_EMOJI} Fresh Wallets:           <b>N/A</b>")
    if overlap is not None:
        emoji = _pct_emoji(overlap, high=30, mid=10)
        lines.append(f"  {emoji} Holder Overlap w/ Scams: <b>{overlap:.0f}%</b>")
    else:
        lines.append(f"  {RISK_UNKNOWN_EMOJI} Holder Overlap w/ Scams: <b>N/A</b>")
    return "\n".join(lines)
