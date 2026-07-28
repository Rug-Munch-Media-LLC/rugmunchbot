#!/usr/bin/env python3
"""
CryptoRugMunch Bot v6 — The Bloomberg Terminal of Shitcoins
============================================================
Production-grade: multi-chain scanning, wallet forensics, tiered subs,
Telegram Stars payments, DEX ref revenue, referral system, AI chat,
inline queries, scheduled tips, trending tokens, spam protection,
website integration, social links, bot profile setup.

Run: cd /root/backend/app/telegram_bot && python3 bot.py
"""

import asyncio
import contextlib
import logging
import re
import secrets
import sys
import time
from datetime import UTC, datetime, time as dtime
from pathlib import Path
from urllib.parse import quote

import httpx
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

# ── Local imports ──
sys.path.insert(0, str(Path(__file__).parent))

import db
from config import (
    BACKEND_URL,
    BOT_DESCRIPTION,
    BOT_SHORT_DESCRIPTION,
    BOT_TOKEN,
    BOT_USERNAME,
    CHAIN_IDS,
    CHANNELS,
    DATABUS_URL,
    DEX_REF_LINKS,
    OWNER_IDS,
    RMI_AUTH_TOKEN,
    SCAM_SCHOOL_TOPICS,
    SUPPORT_EMAIL,
    TELEGRAM_CHANNEL,
    TELEGRAM_GROUP,
    TIERS,
    TOP_UP_PACKS,
    TWITTER_URL,
    WEB_APP_URL,
    WEBSITE_URL,
    fmt_chain,
    fmt_number,
    fmt_pct,
    risk_bar,
    threat_indicator,
)

# ══════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("rmi_bot")

# ══════════════════════════════════════════════
# SPAM PROTECTION
# ══════════════════════════════════════════════
START_TIME: float = time.time()

# Background task tracking — prevents garbage collection of fire-and-forget tasks
_bg_tasks: set[asyncio.Task] = set()

_user_last_action: dict[int, float] = {}
SPAM_COOLDOWN = 3


def check_spam(user_id: int) -> bool:
    now = time.time()
    last = _user_last_action.get(user_id, 0)
    if now - last < SPAM_COOLDOWN and not is_owner(user_id):
        return True
    _user_last_action[user_id] = now
    return False


# ══════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════
EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
TOKEN_RE = re.compile(r"\$([A-Za-z]{2,10})\b")


def is_evm(addr: str) -> bool:
    return bool(EVM_RE.match(addr))


def is_sol(addr: str) -> bool:
    return bool(SOL_RE.match(addr))


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


# Inline constant for symbol-search features (cmd_quick, $TOKEN, cmd_trending).
# All data-fetching routes through DataBus; DexScreener is only used here
# for symbol-based search lookups that DataBus does not yet expose.
# TODO: remove when DataBus adds symbol-search endpoint.
_DEXSCREENER = "https://api.dexscreener.com/latest/dex"


def is_owner(uid: int) -> bool:
    return uid in OWNER_IDS


def sep(char: str = "━", length: int = 28) -> str:
    return char * length


def thin_sep(char: str = "─", length: int = 24) -> str:
    return char * length


def footer_links() -> str:
    """Standard footer with website + social links."""
    return (
        f"\n{thin_sep('·', 28)}\n"
        f'🌐 <a href="{WEBSITE_URL}">rugmunch.io</a> | '
        f'<a href="{TELEGRAM_CHANNEL}">📢 Alerts</a> | '
        f'<a href="{TWITTER_URL}">X</a>'
    )


def web_scan_button(address: str, chain: str = "") -> InlineKeyboardButton:
    """Button to view full scan on the website."""
    url = f"{WEB_APP_URL}?token={address}"
    if chain:
        url += f"&chain={chain}"
    return InlineKeyboardButton("🌐 Full Report on RugMunch.io", url=url)


def dex_buttons(token: str, chain: str) -> list[InlineKeyboardButton]:
    buttons = []
    for dex in DEX_REF_LINKS.values():
        if chain.lower() in dex.get("chains", []):
            url = dex["url"].format(
                token=token,
                chain=chain,
                chain_id=CHAIN_IDS.get(chain.lower(), 1),
            )
            buttons.append(InlineKeyboardButton(f"{dex['emoji']} {dex['name']}", url=url))
    return buttons


def social_proof_text() -> str:
    """Dynamic social proof from DB stats."""
    try:
        conn = db.get_db()
        total_users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        total_scans = conn.execute("SELECT SUM(total_scans) as c FROM users").fetchone()["c"] or 0
        conn.close()
        users_str = f"{total_users:,}" if total_users else "1,000+"
        scans_str = f"{total_scans:,}" if total_scans else "50,000+"
        return f"👥 {users_str} users | 🔍 {scans_str} scans completed"
    except Exception as exc:
        logger.debug("social_proof_text failed", exc_info=True)
        return "👥 1,000+ users | 🔍 50,000+ scans completed"


def paywall_text(user_id: int, action: str = "scan") -> str:
    tier = db.get_user_tier(user_id)
    scans, ai = db.get_weekly_usage(user_id)
    user = db.get_or_create_user(user_id)
    tc = TIERS.get(tier, TIERS["free"])
    limit = tc.get("scans_per_week", 25) if action == "scan" else tc.get("ai_msgs_per_week", 15)
    used = scans if action == "scan" else ai
    bonus = user.get("bonus_scans", 0) if action == "scan" else user.get("bonus_ai_msgs", 0)

    return (
        f"⚡ <b>Weekly {action.title()} Limit Reached</b>\n\n"
        f"📊 <b>{tc['name']} Tier:</b> {used}/{limit} used this week\n"
        f"🎁 <b>Bonus Balance:</b> {bonus} extra\n\n"
        f"<b>Unlock more power:</b>\n\n"
        f"🔍 <b>Scout ($29.99/mo)</b> — 150 scans/wk + bundle detection\n"
        f"🎯 <b>Hunter ($49.99/mo)</b> — 400 scans/wk + wallet forensics\n"
        f"👑 <b>Pro ($99.99/mo)</b> — 1,000 scans/wk + smart money alerts\n\n"
        f"Or buy one-time top-ups — <b>never expire!</b>\n"
        f"🔋 10 scans = ⭐75 | 50 scans = ⭐300 | 100 scans = ⭐500"
    )


def paywall_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⭐ View Plans", callback_data="menu_pricing")],
            [InlineKeyboardButton("🔋 Buy Top-Up", callback_data="menu_topup")],
            [InlineKeyboardButton("🌐 rugmunch.io", url=WEBSITE_URL)],
            [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
        ]
    )


# ══════════════════════════════════════════════
# SCAN ENGINE
# ══════════════════════════════════════════════
def _db_payload(response: dict) -> dict:
    """Extract the actual data payload from a DataBus response (handles wrapper)."""
    if isinstance(response, dict) and "data" in response:
        return response["data"] if isinstance(response["data"], dict) else response
    return response


async def scan_token(address: str, chain: str | None = None) -> dict:
    result = {
        "address": address,
        "chain": chain or detect_chain(address),
        "name": "Unknown",
        "symbol": "???",
        "price": 0,
        "mcap": 0,
        "fdv": 0,
        "volume_24h": 0,
        "liquidity": 0,
        "price_change_1h": 0,
        "price_change_24h": 0,
        "price_change_7d": 0,
        "holders": 0,
        "pair_address": "",
        "dex": "",
        "created_at": None,
        "risk_score": 0,
        "threats": [],
        "contract_verified": None,
        "honeypot": None,
        "buy_tax": None,
        "sell_tax": None,
        "lp_locked": None,
        "mintable": None,
        "can_blacklist": None,
        "owner_renounced": None,
        "top_holders": [],
        "bundle_detected": False,
        "fresh_wallet_pct": 0,
        "exchange_pct": 0,
        "volume_real_ratio": None,
        "dev_wallet": None,
        "dev_other_tokens": [],
        "errors": [],
        "socials": {},
        "_data_source": {"price": None, "security": None, "detail": None},
    }

    async with httpx.AsyncClient(timeout=15) as client:
        # ══════════════════════════════════════════════
        # PHASE 1: DataBus Token Price (cached + fallback chain)
        # Replaces direct DexScreener call — DataBus routes to DexScreener
        # internally with caching, circuit breakers, and rate limiting.
        # Response is the same DexScreener format (pairs array).
        # ══════════════════════════════════════════════
        try:
            r = await client.get(
                f"{DATABUS_URL}/fetch/token_price",
                params={"mint": address},
                timeout=10,
            )
            if r.status_code == 200:
                payload = _db_payload(r.json())
                if payload and isinstance(payload, dict):
                    pairs = payload.get("pairs", [])
                    if pairs:
                        p = pairs[0]
                        result["name"] = p.get("baseToken", {}).get("name", "Unknown")
                        result["symbol"] = p.get("baseToken", {}).get("symbol", "???")
                        result["price"] = float(p.get("priceUsd", 0))
                        result["mcap"] = float(p.get("marketCap") or p.get("fdv") or 0)
                        result["fdv"] = float(p.get("fdv") or result["mcap"])
                        vol = p.get("volume", {})
                        result["volume_24h"] = float(vol.get("h24") or 0)
                        liq = p.get("liquidity", {})
                        result["liquidity"] = float(liq.get("usd") or 0)
                        pc = p.get("priceChange", {})
                        result["price_change_1h"] = pc.get("h1") or 0
                        result["price_change_24h"] = pc.get("h24") or 0
                        result["price_change_7d"] = pc.get("d7") or 0
                        result["pair_address"] = p.get("pairAddress", "")
                        result["dex"] = p.get("dexId", "")
                        result["chain"] = p.get("chainId", result["chain"])
                        result["created_at"] = p.get("pairCreatedAt")
                        socials = p.get("info", {}).get("socials", [])
                        result["socials"] = {s.get("type"): s.get("url") for s in socials}
                        result["_data_source"]["price"] = "databus"
                        logger.info("scan: DataBus token_price OK for %s", short_addr(address))
        except Exception as e:
            result["errors"].append(f"DataBus token_price: {e}")

        # ══════════════════════════════════════════════
        # PHASE 2: DataBus Token Detail (metadata + security summary + on-chain signals)
        # ══════════════════════════════════════════════
        try:
            r = await client.get(
                f"{DATABUS_URL}/token/{address}",
                timeout=10,
                headers={"X-API-Key": RMI_AUTH_TOKEN},
            )
            if r.status_code == 200:
                td = _db_payload(r.json())
                if td:
                    # Fill in missing metadata (Phase 1 DexScreener covers most)
                    if not result["name"] or result["name"] == "Unknown":
                        result["name"] = td.get("name", "Unknown")
                    if not result["symbol"] or result["symbol"] == "???":
                        result["symbol"] = td.get("symbol", "???")
                    if result["price"] == 0 and td.get("price_usd"):
                        result["price"] = float(td["price_usd"])
                    if result["mcap"] == 0 and td.get("market_cap"):
                        result["mcap"] = float(td["market_cap"])
                    if result["liquidity"] == 0 and td.get("liquidity_usd"):
                        result["liquidity"] = float(td["liquidity_usd"])
                if td.get("bundle_detected"):
                    result["bundle_detected"] = True
                    result["threats"].append("Bundle activity detected")
                    result["risk_score"] += 20
                if td.get("fresh_wallet_pct"):
                    result["fresh_wallet_pct"] = td["fresh_wallet_pct"]
                    if td["fresh_wallet_pct"] > 50:
                        result["threats"].append(
                            f"High fresh wallet ratio: {td['fresh_wallet_pct']:.0f}%"
                        )
                        result["risk_score"] += 10
                if td.get("dev_wallet"):
                    result["dev_wallet"] = td["dev_wallet"]
                result["_data_source"]["detail"] = "databus"
                logger.info("scan: DataBus token_detail OK for %s", short_addr(address))
        except Exception as exc:
            logger.debug("scan: DataBus token_detail failed for %s: %s", short_addr(address), exc)

        # ══════════════════════════════════════════════
        # PHASE 3: DataBus Token Security (replaces GoPlus + Honeypot.is)
        # ══════════════════════════════════════════════
        if is_evm(address):
            try:
                r = await client.get(
                    f"{DATABUS_URL}/fetch/token_security",
                    params={"address": address, "chain": result["chain"]},
                    timeout=10,
                )
                if r.status_code == 200:
                    sec = _db_payload(r.json())
                    if sec:
                        result["contract_verified"] = sec.get("is_open_source") or sec.get(
                            "contract_verified"
                        )
                        result["honeypot"] = sec.get("is_honeypot") or sec.get("honeypot")
                        bt = sec.get("buy_tax") or sec.get("buy_tax_pct")
                        result["buy_tax"] = float(bt) if bt else None
                        st = sec.get("sell_tax") or sec.get("sell_tax_pct")
                        result["sell_tax"] = float(st) if st else None
                        result["mintable"] = sec.get("mintable") or sec.get("is_mintable")
                        result["can_blacklist"] = (
                            sec.get("can_blacklist")
                            or sec.get("is_blacklisted")
                            or sec.get("can_take_back_ownership")
                        )
                        result["owner_renounced"] = sec.get("owner_renounced") or sec.get(
                            "is_owner_renounced"
                        )
                        ll = sec.get("lp_locked") or sec.get("lp_locked_pct")
                        result["lp_locked"] = float(ll) if ll else None
                        holders_data = sec.get("holders") or sec.get("top_holders", [])
                        result["holders"] = (
                            len(holders_data) if isinstance(holders_data, list) else 0
                        )
                        for h in (holders_data or [])[:10]:
                            if isinstance(h, dict):
                                raw_pct = float(h.get("pct") or h.get("percent") or 0)
                                # pct is already 0-100 in bot format; DataBus sometimes sends 0-1
                                result["top_holders"].append(
                                    {
                                        "address": h.get("address", ""),
                                        "pct": raw_pct * 100
                                        if "percent" in h and raw_pct < 1
                                        else raw_pct,
                                        "is_contract": bool(
                                            h.get("is_contract") or h.get("contract")
                                        ),
                                        "tag": h.get("tag", ""),
                                    }
                                )
                        # Risk flags from token_security
                        if result["honeypot"]:
                            result["threats"].append("HONEYPOT DETECTED")
                            result["risk_score"] += 40
                        if not result["contract_verified"]:
                            result["threats"].append("Contract not verified")
                            result["risk_score"] += 15
                        if result["mintable"]:
                            result["threats"].append("Mintable supply")
                            result["risk_score"] += 20
                        if result["can_blacklist"]:
                            result["threats"].append("Owner can blacklist")
                            result["risk_score"] += 15
                        if result["owner_renounced"] is False:
                            result["threats"].append("Ownership not renounced")
                            result["risk_score"] += 10
                        if result["buy_tax"] and result["buy_tax"] > 0.1:
                            result["threats"].append(
                                f"High buy tax: {result['buy_tax'] * 100:.1f}%"
                            )
                            result["risk_score"] += 10
                        if result["sell_tax"] and result["sell_tax"] > 0.1:
                            result["threats"].append(
                                f"High sell tax: {result['sell_tax'] * 100:.1f}%"
                            )
                            result["risk_score"] += 15
                        lp_val = result["lp_locked"] if result["lp_locked"] is not None else 1.0
                        if lp_val < 0.5 and result["mcap"] > 10000:
                            result["threats"].append(f"Low LP lock: {lp_val * 100:.0f}%")
                            result["risk_score"] += 15
                        result["_data_source"]["security"] = "databus"
                        logger.info(
                            "scan: DataBus token_security OK for %s",
                            short_addr(address),
                        )
            except Exception as e:
                result["errors"].append(f"DataBus token_security: {e}")

    # ── Volume authenticity ──
    if result["volume_24h"] > 0 and result["mcap"] > 0:
        ratio = result["volume_24h"] / result["mcap"]
        result["volume_real_ratio"] = ratio
        if ratio > 5:
            result["threats"].append(f"Suspicious volume/mcap ratio: {ratio:.1f}x")
            result["risk_score"] += 15

    # ── Liquidity check ──
    if result["mcap"] > 0 and result["liquidity"] > 0:
        liq_ratio = result["liquidity"] / result["mcap"]
        if liq_ratio < 0.02:
            result["threats"].append("Very low liquidity vs mcap")
            result["risk_score"] += 20

    result["risk_score"] = min(result["risk_score"], 100)

    # ── Fire-and-forget: report user-intel to backend scanner ──
    with contextlib.suppress(Exception):
        _bg_task = asyncio.create_task(_report_to_scanner(address, result.get("chain", "solana")))

    return result


async def _report_to_scanner(address: str, chain: str) -> None:
    """Report a user-initiated scan to the rmi-backend scanner (fire-and-forget)."""
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.post(
                f"{BACKEND_URL}/api/v1/scanner/scan",
                json={"chain": chain, "address": address, "depth": "quick"},
                headers={"X-API-Key": RMI_AUTH_TOKEN},
            )
    except Exception as e:
        logger.debug("Backend scanner report failed (non-critical): %s", e)


def format_scan_report(r: dict) -> str:
    chain_str = fmt_chain(r["chain"])
    risk = r["risk_score"]
    d = thin_sep()
    s = sep()

    # Age calculation
    age_str = ""
    if r.get("created_at"):
        try:
            created = datetime.fromtimestamp(r["created_at"] / 1000, tz=UTC)
            age = datetime.now(UTC) - created
            if age.days > 365:
                age_str = f" | Age: {age.days // 365}y {age.days % 365 // 30}mo"
            elif age.days > 0:
                age_str = f" | Age: {age.days}d"
            else:
                age_str = f" | Age: {age.seconds // 3600}h"
        except Exception:
            pass

    lines = [
        "🛡️ <b>RMI Token Scan</b>",
        s,
        f"<b>{r['name']}</b> ({r['symbol']}) {chain_str}{age_str}",
        f"<code>{r['address']}</code>",
        "",
        "📊 <b>Market Data</b>",
        d,
        f"💰 Price: <b>{fmt_number(r['price'])}</b>",
        f"📈 Market Cap: <b>{fmt_number(r['mcap'])}</b>",
        f"💧 Liquidity: <b>{fmt_number(r['liquidity'])}</b>",
        f"📊 24h Volume: <b>{fmt_number(r['volume_24h'])}</b>",
        f"🔄 1h: {fmt_pct(r['price_change_1h'])} | 24h: {fmt_pct(r['price_change_24h'])} | 7d: {fmt_pct(r['price_change_7d'])}",
        "",
        "🔒 <b>Security</b>",
        d,
        risk_bar(risk),
    ]

    if r["threats"]:
        lines.append("")
        for t in r["threats"]:
            lines.append(f"  🔴 {t}")
    else:
        lines.append("  ✅ No threats detected")

    # Contract details
    cl = []
    if r["honeypot"] is not None:
        cl.append(threat_indicator("Honeypot", r["honeypot"]))
    if r["contract_verified"] is not None:
        cl.append(threat_indicator("Verified", not r["contract_verified"]))
    if r["mintable"] is not None:
        cl.append(threat_indicator("Mintable", r["mintable"]))
    if r["owner_renounced"] is not None:
        cl.append(threat_indicator("Ownership Renounced", not r["owner_renounced"]))
    if r["lp_locked"] is not None:
        cl.append(f"🔐 LP Locked: {r['lp_locked'] * 100:.0f}%")
    if r["buy_tax"] is not None:
        cl.append(f"📥 Buy Tax: {r['buy_tax'] * 100:.1f}%")
    if r["sell_tax"] is not None:
        cl.append(f"📤 Sell Tax: {r['sell_tax'] * 100:.1f}%")
    if cl:
        lines.append("")
        lines.extend(cl)

    # Holders
    if r["top_holders"]:
        lines.extend(["", "👥 <b>Top Holders</b>", d])
        for i, h in enumerate(r["top_holders"][:5], 1):
            tag = f" [{h['tag']}]" if h.get("tag") else ""
            cf = " 📄" if h.get("is_contract") else ""
            lines.append(
                f"  {i}. <code>{short_addr(h['address'])}</code> — {h['pct']:.1f}%{tag}{cf}"
            )

    # Advanced
    adv = []
    if r["bundle_detected"]:
        adv.append("🕸️ Bundle activity detected")
    if r["fresh_wallet_pct"] > 0:
        adv.append(f"🆕 Fresh wallets: {r['fresh_wallet_pct']:.0f}%")
    if r["volume_real_ratio"] is not None:
        adv.append(f"📊 Vol/MCap ratio: {r['volume_real_ratio']:.2f}x")
    if r["dev_wallet"]:
        adv.append(f"👨‍💻 Dev: <code>{short_addr(r['dev_wallet'])}</code>")
    if adv:
        lines.extend(["", "🔬 <b>Advanced Analysis</b>", d])
        lines.extend(adv)

    # Social links from DexScreener
    socials = r.get("socials", {})
    if socials:
        soc_parts = []
        for stype, surl in socials.items():
            if stype == "twitter":
                soc_parts.append(f'<a href="{surl}">X</a>')
            elif stype == "telegram":
                soc_parts.append(f'<a href="{surl}">📱</a>')
            elif stype == "website":
                soc_parts.append(f'<a href="{surl}">🌐</a>')
        if soc_parts:
            lines.extend(["", f"🔗 Socials: {' | '.join(soc_parts)}"])

    # Trade section
    lines.extend(["", s, "💱 <b>Trade:</b>"])

    # Footer
    lines.append(footer_links())

    return "\n".join(lines)


# ══════════════════════════════════════════════
# WALLET FORENSICS ENGINE
# ══════════════════════════════════════════════
async def analyze_wallet(address: str, chain: str | None = None) -> dict:
    result = {
        "address": address,
        "chain": chain or detect_chain(address),
        "balance_usd": 0,
        "tx_count": 0,
        "first_seen": None,
        "wallet_age_days": 0,
        "top_tokens": [],
        "is_fresh": False,
        "is_exchange": False,
        "is_contract": False,
        "risk_score": 0,
        "flags": [],
        "recent_txs": [],
        "funding_source": None,
        "connected_wallets": [],
        "pnl_estimate": None,
        "errors": [],
    }
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(
                f"{BACKEND_URL}/api/v1/databus/wallet/{address}",
                timeout=10,
                headers={"X-API-Key": RMI_AUTH_TOKEN},
            )
            if r.status_code == 200:
                data = r.json()
                for k in [
                    "balance_usd",
                    "tx_count",
                    "first_seen",
                    "is_exchange",
                    "is_contract",
                    "risk_score",
                    "flags",
                    "funding_source",
                    "pnl_estimate",
                ]:
                    if k in data:
                        result[k] = data[k]
                result["top_tokens"] = data.get("top_tokens", [])[:10]
                result["recent_txs"] = data.get("recent_txs", [])[:5]
                result["connected_wallets"] = data.get("connected_wallets", [])[:5]
                if result["first_seen"]:
                    try:
                        first = datetime.fromisoformat(result["first_seen"].replace("Z", "+00:00"))
                        result["wallet_age_days"] = (datetime.now(UTC) - first).days
                        result["is_fresh"] = result["wallet_age_days"] < 7
                    except Exception:
                        pass
                return result
        except Exception:
            pass
        if is_sol(address):
            try:
                r = await client.get(f"https://api.solana.fm/v0/accounts/{address}")
                if r.status_code == 200:
                    data = r.json()
                    result["balance_usd"] = float(data.get("balance", 0)) / 1e9 * 150
                    result["tx_count"] = data.get("transaction_count", 0)
            except Exception as e:
                result["errors"].append(f"Solana.fm: {e}")
    return result


def format_wallet_report(r: dict) -> str:
    d = thin_sep()
    s = sep()
    age_str = f"{r['wallet_age_days']} days" if r["wallet_age_days"] else "Unknown"
    lines = [
        "👛 <b>Wallet Analysis</b>",
        s,
        f"<code>{r['address']}</code>",
        f"{fmt_chain(r['chain'])} | Age: {age_str}",
        "",
        "📊 <b>Overview</b>",
        d,
        f"💰 Balance: <b>{fmt_number(r['balance_usd'])}</b>",
        f"📝 Transactions: <b>{r['tx_count']:,}</b>" if r["tx_count"] else "📝 Transactions: N/A",
        f"🏷️ Type: {'Exchange' if r['is_exchange'] else 'Contract' if r['is_contract'] else 'EOA (Wallet)'}",
    ]
    if r["is_fresh"]:
        lines.append("🆕 <b>FRESH WALLET</b> (<7 days) — ⚠️ Higher risk")
    if r["flags"]:
        lines.extend(["", "⚠️ <b>Flags</b>", d])
        for flag in r["flags"]:
            lines.append(f"  🔴 {flag}")
    if r["pnl_estimate"] is not None:
        pnl_e = "📈" if r["pnl_estimate"] >= 0 else "📉"
        lines.extend(["", f"{pnl_e} <b>Estimated P&L:</b> {fmt_number(r['pnl_estimate'])}"])
    if r["top_tokens"]:
        lines.extend(["", "🪙 <b>Top Holdings</b>", d])
        for i, t in enumerate(r["top_tokens"][:5], 1):
            lines.append(
                f"  {i}. <b>{t.get('symbol', '???')}</b> — {fmt_number(t.get('value_usd', 0))}"
            )
    if r["funding_source"]:
        lines.extend(["", f"🏦 <b>Funding Source:</b> {r['funding_source']}"])
    if r["connected_wallets"]:
        lines.extend(["", "🔗 <b>Connected Wallets</b>", d])
        for w in r["connected_wallets"][:5]:
            lines.append(f"  • <code>{short_addr(w)}</code>")
    if r["risk_score"] > 0:
        lines.extend(["", risk_bar(r["risk_score"])])
    lines.append(footer_links())
    return "\n".join(lines)


# ══════════════════════════════════════════════
# KEYBOARD BUILDERS
# ══════════════════════════════════════════════
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔍 Scan Token", callback_data="menu_scan"),
                InlineKeyboardButton("👛 Wallet Check", callback_data="menu_wallet"),
            ],
            [
                InlineKeyboardButton("📊 Technical Analysis", callback_data="menu_ta"),
                InlineKeyboardButton("🔥 Trending", callback_data="menu_trending"),
            ],
            [
                InlineKeyboardButton("👀 Watchlist", callback_data="menu_watchlist"),
                InlineKeyboardButton("🔔 Alerts", callback_data="menu_alerts"),
            ],
            [
                InlineKeyboardButton("📋 My Account", callback_data="menu_account"),
                InlineKeyboardButton("⭐ Upgrade", callback_data="menu_pricing"),
            ],
            [
                InlineKeyboardButton("📚 Scam School", callback_data="menu_scamschool"),
                InlineKeyboardButton("🤝 Refer & Earn", callback_data="menu_refer"),
            ],
            [
                InlineKeyboardButton("🌐 rugmunch.io", url=WEBSITE_URL),
                InlineKeyboardButton("📢 Alerts Channel", url=TELEGRAM_CHANNEL),
            ],
        ]
    )


def scan_result_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    rows = [
        [web_scan_button(address, chain)],
        [
            InlineKeyboardButton("🔬 Deep Scan (250★)", callback_data=f"deep_{address}_{chain}"),
            InlineKeyboardButton("⭐ Watch", callback_data=f"watch_{address}_{chain}"),
        ],
    ]
    dex_btns = dex_buttons(address, chain)
    for i in range(0, len(dex_btns), 2):
        rows.append(dex_btns[i : i + 2])
    rows.append([InlineKeyboardButton("◀️ Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def pricing_kb() -> InlineKeyboardMarkup:
    rows = []
    for key in ["scout", "hunter", "pro"]:
        t = TIERS[key]
        rows.append(
            [
                InlineKeyboardButton(
                    f"{t['emoji']} {t['name']} — ${t['price_monthly']}/mo",
                    callback_data=f"sub_{key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("🌐 Compare Plans on Website", url=f"{WEBSITE_URL}/pricing")])
    rows.append([InlineKeyboardButton("📧 Enterprise", url=f"mailto:{SUPPORT_EMAIL}")])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def topup_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, pack in TOP_UP_PACKS.items():
        rows.append(
            [
                InlineKeyboardButton(
                    f"{pack['emoji']} {pack['name']} — ⭐{pack['stars']}",
                    callback_data=f"topup_{key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def scamschool_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, topic in SCAM_SCHOOL_TOPICS.items():
        rows.append([InlineKeyboardButton(topic["title"], callback_data=f"scam_{key}")])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Main Menu", callback_data="menu_main")]])


def social_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🌐 Website", url=WEBSITE_URL),
                InlineKeyboardButton("X Twitter", url=TWITTER_URL),
            ],
            [
                InlineKeyboardButton("📢 Alerts", url=TELEGRAM_CHANNEL),
                InlineKeyboardButton("💬 Chat", url=TELEGRAM_GROUP),
            ],
        ]
    )


# ══════════════════════════════════════════════
# COMMAND HANDLERS
# ══════════════════════════════════════════════


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bot health status: uptime, database, and backend connectivity.

    Provides operators and users with a quick overview of service health.

    Args:
        update: The incoming Telegram update.
        ctx: The application context.
    """
    uptime_seconds = int(time.time() - START_TIME)
    days, remainder = divmod(uptime_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"

    # Database status
    db_ok = False
    user_count = 0
    scan_count = 0
    try:
        conn = db.get_db()
        user_count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        scan_count = conn.execute("SELECT SUM(total_scans) as c FROM users").fetchone()["c"] or 0
        conn.close()
        db_ok = True
    except Exception as exc:
        logger.debug("Status DB check failed", exc_info=True)

    # Backend health
    backend_ok = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{BACKEND_URL}/api/v1/health")
            backend_ok = r.status_code == 200
    except Exception:
        backend_ok = False

    db_status = "✅ Connected" if db_ok else "❌ Disconnected"
    backend_status = "✅ Connected" if backend_ok else "❌ Unreachable"

    text = (
        f"📊 <b>Bot Status</b>\n{sep()}\n\n"
        f"⏱ <b>Uptime:</b> {uptime_str}\n"
        f"🗄 <b>Database:</b> {db_status}\n"
        f"🔗 <b>Backend:</b> {backend_status}\n"
        f"👥 <b>Users:</b> {user_count:,}\n"
        f"🔍 <b>Total Scans:</b> {scan_count:,}\n\n"
        f"{footer_links()}"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
        disable_web_page_preview=True,
    )


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome message with quick-start guide and referral handling.

    Displays an overview of bot capabilities and a menu of actions.
    Processes referral codes from /start ref_<code> links.

    Args:
        update: The incoming Telegram update.
        ctx: The application context (args may contain referral code).
    """
    user = update.effective_user
    if user is None:
        return
    u = db.get_or_create_user(user.id, user.username, user.first_name)

    # Referral handling
    if ctx.args and ctx.args[0].startswith("ref_"):
        ref_code = ctx.args[0][4:]
        if ref_code != u.get("referral_code") and not u.get("referred_by"):
            conn = db.get_db()
            referrer = conn.execute(
                "SELECT user_id FROM users WHERE referral_code = ?", (ref_code,)
            ).fetchone()
            if referrer:
                conn.execute(
                    "UPDATE users SET referred_by = ?, bonus_scans = bonus_scans + 5 WHERE user_id = ?",
                    (ref_code, user.id),
                )
                conn.execute(
                    "UPDATE users SET bonus_scans = bonus_scans + 5 WHERE user_id = ?",
                    (referrer["user_id"],),
                )
                conn.commit()
            conn.close()

    proof = social_proof_text()
    text = (
        f"🛡️ <b>RugMunch Intelligence</b>\n"
        f"<i>The Bloomberg Terminal of Shitcoins</i>\n\n"
        f"{proof}\n\n"
        f"<b>What I can do for you:</b>\n\n"
        f"🔍 <b>Token Scanning</b>\n"
        f"  Honeypots, rug pulls, bundles, fake volume\n"
        f"  across 77+ chains in seconds\n\n"
        f"👛 <b>Wallet Forensics</b>\n"
        f"  Track smart money, dev wallets, funding sources\n\n"
        f"📊 <b>Market Intelligence</b>\n"
        f"  Technical analysis, trending tokens, alerts\n\n"
        f"🤖 <b>AI-Powered Risk Scoring</b>\n"
        f"  Advanced detection that goes beyond surface-level\n\n"
        f"<b>Quick Start:</b>\n"
        f"  📌 Paste any contract address → auto-scan\n"
        f"  📌 Type <code>$TOKEN</code> → quick price lookup\n"
        f"  📌 /scan <code>address</code> → full analysis\n"
        f"  📌 /help → see all commands\n\n"
        f"🆓 <b>Free:</b> {TIERS['free']['scans_per_week']} scans + {TIERS['free']['ai_msgs_per_week']} AI msgs/week\n"
        f"⭐ <b>Premium from $29.99/mo</b> — unlock everything\n\n"
        f'🌐 <a href="{WEBSITE_URL}">rugmunch.io</a> — full web scanner'
    )

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔍 Scan a Token", callback_data="menu_scan")],
            [
                InlineKeyboardButton("⭐ View Premium Plans", callback_data="menu_pricing"),
                InlineKeyboardButton("📚 Learn About Scams", callback_data="menu_scamschool"),
            ],
            [
                InlineKeyboardButton("🌐 rugmunch.io", url=WEBSITE_URL),
                InlineKeyboardButton("📢 Join Alerts", url=TELEGRAM_CHANNEL),
            ],
        ]
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = thin_sep()
    text = (
        f"📖 <b>RugMunch Intelligence — Command Guide</b>\n"
        f"{sep()}\n\n"
        f"🔍 <b>TOKEN ANALYSIS</b>\n{d}\n"
        f"/scan <code>address</code> — Full security scan\n"
        f"/ta <code>address</code> — Technical analysis & signals\n"
        f"/compare <code>addr1</code> <code>addr2</code> — Side-by-side\n"
        f"/quick <code>symbol</code> — Quick price check (same as $TOKEN)\n\n"
        f"👛 <b>WALLET INTELLIGENCE</b>\n{d}\n"
        f"/wallet <code>address</code> — Forensic wallet analysis\n"
        f"/watch <code>address</code> [chain] — Add to watchlist\n"
        f"/unwatch <code>address</code> — Remove from watchlist\n"
        f"/watchlist — View your watchlist\n"
        f"/alerts — Price & activity alerts\n\n"
        f"📊 <b>MARKET DATA</b>\n{d}\n"
        f"/trending — Hot tokens right now\n"
        f"/news — Latest crypto news\n\n"
        f"💳 <b>ACCOUNT & BILLING</b>\n{d}\n"
        f"/account — Dashboard & usage stats\n"
        f"/pricing — Subscription plans\n"
        f"/topup — Buy extra scans (no expiry)\n"
        f"/refer — Invite friends, earn scans\n\n"
        f"📚 <b>EDUCATION</b>\n{d}\n"
        f"/scamschool — Learn about crypto scams\n"
        f"/rugcheck — Quick rug pull checklist\n\n"
        f"💡 <b>PRO TIPS</b>\n{d}\n"
        f"• Paste any <code>0x...</code> address to auto-scan\n"
        f"• Type <code>$PEPE</code> for instant price check\n"
        f"• Use inline mode: <code>@RugMunchBot address</code> in any chat\n"
        f"• Upgrade for bundle detection, wallet forensics & more\n"
        f"• Use /refer to earn free scans\n\n"
        f'🌐 <a href="{WEBSITE_URL}">rugmunch.io</a> — Full web scanner with charts'
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🌐 rugmunch.io", url=WEBSITE_URL),
                InlineKeyboardButton("💬 Support", url=TELEGRAM_GROUP),
            ],
            [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
        ]
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True
    )


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.get_or_create_user(user.id, user.username, user.first_name)
    if check_spam(user.id):
        return
    if not ctx.args:
        await update.message.reply_text(
            "🔍 <b>Token Scanner</b>\n\n"
            "Usage: /scan <code>address</code> [chain]\n\n"
            "<b>Examples:</b>\n"
            "  /scan <code>0x6982508145454Ce325dDbE47a25d4ec3d2311933</code>\n"
            "  /scan <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code> solana\n\n"
            "💡 <i>Or just paste a contract address directly!</i>",
            parse_mode=ParseMode.HTML,
        )
        return
    target = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else None
    if not is_owner(user.id):
        allowed, _used, _limit = db.check_rate_limit(user.id, "scan")
        if not allowed:
            await update.message.reply_text(
                paywall_text(user.id, "scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=paywall_kb(),
            )
            return
    msg = await update.message.reply_text(
        f"🔍 <b>Scanning token...</b>\n<code>{short_addr(target)}</code>\n\n⏳ Querying RMI DataBus...",
        parse_mode=ParseMode.HTML,
    )
    try:
        result = await scan_token(target, chain)
        # DataBus completely failed — no price or detail data
        if result["_data_source"]["price"] is None and result["_data_source"]["detail"] is None:
            await msg.edit_text(
                f"❌ <b>Scanner Temporarily Unavailable</b>\n\n"
                f"RMI DataBus could not fetch data for <code>{short_addr(target)}</code>.\n\n"
                f"Please try again later.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        if result["name"] == "Unknown" and not result["errors"]:
            await msg.edit_text(
                f"❌ <b>Token Not Found</b>\n\n"
                f"No data found for <code>{short_addr(target)}</code>.\n\n"
                f"• Check the address is correct\n"
                f"• Ensure the token has a trading pair\n"
                f"• Try specifying the chain: /scan <code>addr</code> solana",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        report = format_scan_report(result)
        db.increment_usage(user.id, scan=True)
        db.log_scan(user.id, target, result["chain"], "basic", result["risk_score"])
        await msg.edit_text(
            report,
            parse_mode=ParseMode.HTML,
            reply_markup=scan_result_kb(target, result["chain"]),
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"Scan error: {e}")
        await msg.edit_text(
            f"❌ <b>Scan Failed</b>\n\nError: {str(e)[:200]}\n\nPlease check the address and try again.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def cmd_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text(
            "👛 <b>Wallet Forensics</b>\n\n"
            "Usage: /wallet <code>address</code>\n\n"
            "<b>Example:</b>\n"
            "  /wallet <code>0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045</code>\n\n"
            "<i>Hunter+ tiers get full P&L, funding source tracing, and connected wallet analysis.</i>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            await update.message.reply_text(
                paywall_text(user.id, "scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=paywall_kb(),
            )
            return
    addr = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else None
    msg = await update.message.reply_text(
        "👛 <b>Analyzing wallet...</b>\n⏳ Querying on-chain data...",
        parse_mode=ParseMode.HTML,
    )
    try:
        result = await analyze_wallet(addr, chain)
        report = format_wallet_report(result)
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            report,
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"Wallet error: {e}")
        await msg.edit_text(
            f"❌ <b>Wallet Analysis Failed</b>\n\n{str(e)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def cmd_ta(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text(
            "📊 <b>Technical Analysis</b>\n\nUsage: /ta <code>address_or_symbol</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            await update.message.reply_text(
                paywall_text(user.id, "scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=paywall_kb(),
            )
            return
    target = ctx.args[0]
    msg = await update.message.reply_text(
        "📊 <b>Running Technical Analysis...</b>", parse_mode=ParseMode.HTML
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{DATABUS_URL}/fetch/token_price",
                params={"mint": target},
                timeout=10,
            )
            if r.status_code != 200:
                await msg.edit_text(
                    "❌ Token not found via RMI DataBus.", parse_mode=ParseMode.HTML
                )
                return
            payload = _db_payload(r.json())
            pairs = payload.get("pairs", []) if isinstance(payload, dict) else []
            if not pairs:
                await msg.edit_text("❌ No trading pairs found.", parse_mode=ParseMode.HTML)
                return
            p = pairs[0]
            name = p.get("baseToken", {}).get("name", "???")
            symbol = p.get("baseToken", {}).get("symbol", "???")
            price = float(p.get("priceUsd", 0))
            pc = p.get("priceChange", {})
            vol = p.get("volume", {})
            txns = p.get("txns", {})
            signals = []
            h1 = pc.get("h1") or 0
            h24 = pc.get("h24") or 0
            d7 = pc.get("d7") or 0
            if h1 > 5 and h24 > 10:
                signals.append("🟢 Strong short-term momentum")
            elif h1 < -5 and h24 < -10:
                signals.append("🔴 Strong downtrend")
            if h24 > 0 > h1:
                signals.append("🟡 Pullback in uptrend")
            if h24 < 0 < h1:
                signals.append("🟡 Bounce in downtrend")
            v24 = float(vol.get("h24") or 0)
            v6 = float(vol.get("h6") or 0)
            if v6 > 0 and v24 > 0:
                vol_trend = v6 / (v24 / 4)
                if vol_trend > 1.5:
                    signals.append("📈 Volume increasing (acceleration)")
                elif vol_trend < 0.5:
                    signals.append("📉 Volume declining (loss of interest)")
            buys = txns.get("h24", {}).get("buys", 0)
            sells = txns.get("h24", {}).get("sells", 0)
            total_tx = buys + sells
            if total_tx > 0:
                buy_ratio = buys / total_tx
                if buy_ratio > 0.7:
                    signals.append(f"🟢 Buy pressure dominant ({buy_ratio * 100:.0f}% buys)")
                elif buy_ratio < 0.3:
                    signals.append(
                        f"🔴 Sell pressure dominant ({(1 - buy_ratio) * 100:.0f}% sells)"
                    )
                else:
                    signals.append(f"⚪ Balanced buy/sell ({buy_ratio * 100:.0f}% buys)")
            text = (
                f"📊 <b>Technical Analysis</b>\n{sep()}\n"
                f"<b>{name}</b> ({symbol})\n<code>{target}</code>\n\n"
                f"💰 Price: <b>{fmt_number(price)}</b>\n"
                f"🔄 1h: {fmt_pct(h1)} | 24h: {fmt_pct(h24)} | 7d: {fmt_pct(d7)}\n\n"
                f"📈 <b>Volume</b>\n{thin_sep()}\n"
                f"6h: {fmt_number(v6)} | 24h: {fmt_number(v24)}\n"
                f"Txns 24h: {total_tx:,} (B:{buys:,} S:{sells:,})\n\n"
                f"🧭 <b>Signals</b>\n{thin_sep()}\n"
            )
            if signals:
                text += "\n".join(signals)
            else:
                text += "⚪ No strong signals detected"
            text += "\n\n<i>TA is not financial advice. Always /scan for security.</i>"
            text += footer_links()
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"TA error: {e}")
        await msg.edit_text(
            f"❌ TA failed: {str(e)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def cmd_compare(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "⚖️ <b>Compare Tokens</b>\n\nUsage: /compare <code>addr1</code> <code>addr2</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            await update.message.reply_text(
                paywall_text(user.id, "scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=paywall_kb(),
            )
            return
    msg = await update.message.reply_text("⚖️ <b>Comparing tokens...</b>", parse_mode=ParseMode.HTML)
    try:
        r1, r2 = await asyncio.gather(scan_token(ctx.args[0]), scan_token(ctx.args[1]))

        def mini(r):
            return (
                f"<b>{r['name']}</b> ({r['symbol']})\n"
                f"💰 {fmt_number(r['price'])} | MCap: {fmt_number(r['mcap'])}\n"
                f"💧 Liq: {fmt_number(r['liquidity'])} | Vol: {fmt_number(r['volume_24h'])}\n"
                f"🔄 24h: {fmt_pct(r['price_change_24h'])}\n"
                f"⚠️ Risk: {r['risk_score']}% | Threats: {len(r['threats'])}"
            )

        text = f"⚖️ <b>Token Comparison</b>\n{sep()}\n\n1️⃣ {mini(r1)}\n\n2️⃣ {mini(r2)}\n\n{thin_sep()}\n🏆 <b>Verdict:</b> "
        if r1["risk_score"] < r2["risk_score"]:
            text += f"{r1['name']} has lower risk ({r1['risk_score']}% vs {r2['risk_score']}%)"
        elif r2["risk_score"] < r1["risk_score"]:
            text += f"{r2['name']} has lower risk ({r2['risk_score']}% vs {r1['risk_score']}%)"
        else:
            text += "Both have similar risk scores"
        text += footer_links()
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
            disable_web_page_preview=True,
        )
    except Exception as e:
        await msg.edit_text(
            f"❌ Compare failed: {str(e)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def cmd_quick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Quick price check — same as $TOKEN but as a command."""
    if not ctx.args:
        await update.message.reply_text(
            "🪙 Usage: /quick <code>SYMBOL</code>\nExample: /quick PEPE",
            parse_mode=ParseMode.HTML,
        )
        return
    symbol = ctx.args[0].upper().lstrip("$")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_DEXSCREENER}/search/?q={symbol}")
            if r.status_code == 200:
                pairs = r.json().get("pairs", [])
                if pairs:
                    p = pairs[0]
                    addr = p.get("baseToken", {}).get("address", "")
                    chain = p.get("chainId", "")
                    price = float(p.get("priceUsd", 0))
                    h24 = p.get("priceChange", {}).get("h24") or 0
                    vol = float(p.get("volume", {}).get("h24") or 0)
                    mcap = float(p.get("marketCap") or 0)
                    name = p.get("baseToken", {}).get("name", symbol)
                    text = (
                        f"🪙 <b>{name}</b> ({symbol}) {fmt_chain(chain)}\n"
                        f"<code>{addr}</code>\n\n"
                        f"💰 {fmt_number(price)} | 🔄 24h: {fmt_pct(h24)}\n"
                        f"📈 MCap: {fmt_number(mcap)} | 💧 Vol: {fmt_number(vol)}"
                        f"{footer_links()}"
                    )
                    kb = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🔍 Full Scan", callback_data=f"scan_{addr}_{chain}"
                                )
                            ],
                            [web_scan_button(addr, chain)],
                        ]
                    )
                    await update.message.reply_text(
                        text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=kb,
                        disable_web_page_preview=True,
                    )
                    return
    except Exception:
        pass
    await update.message.reply_text(
        f"❌ Could not find token: <b>{symbol}</b>", parse_mode=ParseMode.HTML
    )


async def cmd_rugcheck(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Quick rug pull checklist — educational."""
    text = (
        f"🚩 <b>Rug Pull Checklist</b>\n{sep()}\n\n"
        f"Before buying ANY token, check these:\n\n"
        f"<b>🔴 RED FLAGS (Run!)</b>\n{thin_sep()}\n"
        f"  ❌ Honeypot detected (can't sell)\n"
        f"  ❌ Unrenounced ownership\n"
        f"  ❌ Mintable supply (infinite tokens)\n"
        f"  ❌ Blacklist function exists\n"
        f"  ❌ LP not locked (< 50%)\n"
        f"  ❌ Buy/sell tax > 10%\n\n"
        f"<b>🟡 YELLOW FLAGS (Caution)</b>\n{thin_sep()}\n"
        f"  ⚠️ Bundle activity detected\n"
        f"  ⚠️ >50% fresh wallets (<7 days)\n"
        f"  ⚠️ Dev wallet holds >20%\n"
        f"  ⚠️ Suspicious volume/mcap ratio\n"
        f"  ⚠️ Very low liquidity vs mcap\n\n"
        f"<b>🟢 GREEN FLAGS (Safer)</b>\n{thin_sep()}\n"
        f"  ✅ Contract verified\n"
        f"  ✅ Ownership renounced\n"
        f"  ✅ LP locked > 90%\n"
        f"  ✅ No blacklist/mint functions\n"
        f"  ✅ Diverse holder distribution\n"
        f"  ✅ Organic volume patterns\n\n"
        f"<b>🛡️ Always run /scan before buying!</b>"
        f"{footer_links()}"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
        disable_web_page_preview=True,
    )


async def cmd_trending(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "🔥 <b>Fetching trending tokens...</b>", parse_mode=ParseMode.HTML
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_DEXSCREENER}/search/?q=trending")
            if r.status_code == 200:
                pairs = r.json().get("pairs", [])[:8]
                if not pairs:
                    await msg.edit_text("❌ No trending data available.", parse_mode=ParseMode.HTML)
                    return
                lines = ["🔥 <b>Trending Tokens</b>", sep(), thin_sep()]
                for i, p in enumerate(pairs, 1):
                    name = p.get("baseToken", {}).get("name", "???")[:15]
                    symbol = p.get("baseToken", {}).get("symbol", "???")
                    price = float(p.get("priceUsd", 0))
                    h24 = p.get("priceChange", {}).get("h24") or 0
                    vol = float(p.get("volume", {}).get("h24") or 0)
                    chain = p.get("chainId", "?")
                    addr = p.get("baseToken", {}).get("address", "")
                    emoji = "🟢" if h24 >= 0 else "🔴"
                    lines.append(
                        f"{i}. {emoji} <b>{name}</b> ({symbol}) {chain}\n   {fmt_number(price)} | {fmt_pct(h24)} | Vol: {fmt_number(vol)}\n   <code>{short_addr(addr)}</code>"
                    )
                lines.append(
                    f'\n{thin_sep()}\nTap address to /scan | <a href="{WEB_APP_URL}">View all on rugmunch.io</a>'
                )
                await msg.edit_text(
                    "\n".join(lines),
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                    disable_web_page_preview=True,
                )
            else:
                await msg.edit_text("❌ Could not fetch trending data.", parse_mode=ParseMode.HTML)
    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)[:200]}", parse_mode=ParseMode.HTML)


async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{BACKEND_URL}/api/v1/news/latest?limit=5",
                headers={"X-API-Key": RMI_AUTH_TOKEN},
            )
            if r.status_code == 200:
                articles = r.json()
                if articles:
                    lines = ["📰 <b>Crypto News</b>", sep(), thin_sep()]
                    for a in articles[:5]:
                        title = a.get("title", "Untitled")[:60]
                        source = a.get("source", "News")
                        lines.append(f"• <b>{title}</b>\n  <i>{source}</i>")
                    lines.append(
                        f'\n{thin_sep()}\n🌐 <a href="{WEBSITE_URL}/news">More on rugmunch.io</a>'
                    )
                    await update.message.reply_text(
                        "\n".join(lines),
                        parse_mode=ParseMode.HTML,
                        reply_markup=back_kb(),
                        disable_web_page_preview=True,
                    )
                    return
    except Exception:
        pass
    await update.message.reply_text(
        f"📰 <b>Crypto News</b>\n\n"
        f"News feed loading...\n\n"
        f'📢 Follow <a href="{TELEGRAM_CHANNEL}">@RugMunchAlerts</a> for real-time alerts!\n'
        f'🌐 <a href="{WEBSITE_URL}">rugmunch.io</a>',
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
        disable_web_page_preview=True,
    )


async def cmd_account(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = db.get_or_create_user(user.id, user.username, user.first_name)
    tier = db.get_user_tier(user.id)
    tc = TIERS.get(tier, TIERS["free"])
    scans, ai = db.get_weekly_usage(user.id)
    stats = db.get_user_stats(user.id)
    tier_badge = f"{tc['emoji']} {tc['name']}"
    if u.get("tier_expires") and u["tier_expires"] > 0:
        exp = datetime.fromtimestamp(u["tier_expires"], tz=UTC).strftime("%b %d, %Y")
        tier_badge += f" (expires {exp})"

    # Premium nudge for free users
    nudge = ""
    if tier == "free":
        nudge = (
            f"\n{thin_sep()}\n"
            f"⭐ <b>Upgrade to unlock:</b>\n"
            f"  • Bundle detection & fake volume alerts\n"
            f"  • Wallet forensics & smart money tracking\n"
            f"  • Real-time price alerts & watchlist\n"
            f"  • Up to 1,000 scans/week\n"
        )

    text = (
        f"👤 <b>Account Dashboard</b>\n{sep()}\n"
        f"<b>User:</b> @{user.username or user.first_name}\n"
        f"<b>Tier:</b> {tier_badge}\n\n"
        f"📊 <b>This Week</b>\n{thin_sep()}\n"
        f"🔍 Scans: <b>{scans}/{tc.get('scans_per_week', 25)}</b>\n"
        f"🤖 AI Messages: <b>{ai}/{tc.get('ai_msgs_per_week', 15)}</b>\n\n"
        f"🎁 <b>Bonus Balance</b>\n{thin_sep()}\n"
        f"🔬 Extra Scans: {u.get('bonus_scans', 0)}\n"
        f"🤖 Extra AI Msgs: {u.get('bonus_ai_msgs', 0)}\n"
        f"⭐ Stars: {u.get('stars_balance', 0)}\n\n"
        f"📈 <b>All-Time</b>\n{thin_sep()}\n"
        f"Total Scans: {u.get('total_scans', 0)}\n"
        f"Watchlist: {stats.get('watchlist_count', 0)} tokens\n"
        f"{nudge}"
        f"{footer_links()}"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⭐ Upgrade Plan", callback_data="menu_pricing"),
                InlineKeyboardButton("🔋 Top Up", callback_data="menu_topup"),
            ],
            [InlineKeyboardButton("🌐 rugmunch.io", url=WEBSITE_URL)],
            [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
        ]
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True
    )


async def cmd_pricing(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = thin_sep()
    text = f"⭐ <b>Subscription Plans</b>\n{sep()}\n\n"
    for key, t in TIERS.items():
        if key == "free":
            text += f"{t['emoji']} <b>{t['name']}</b> — $0\n{d}\n"
        else:
            text += f"{t['emoji']} <b>{t['name']}</b> — <b>${t['price_monthly']}/mo</b>\n{d}\n"
        for f in t["features"]:
            text += f"  • {f}\n"
        text += "\n"
    text += (
        f"{sep()}\n"
        f"💳 Pay with Telegram Stars\n"
        f"🔄 Weekly reset Monday 00:00 UTC\n\n"
        f'🌐 <a href="{WEBSITE_URL}/pricing">Compare plans on rugmunch.io</a>\n'
        f"📧 Enterprise: {SUPPORT_EMAIL}"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=pricing_kb(),
        disable_web_page_preview=True,
    )


async def cmd_topup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        f"🔋 <b>Top Up — Buy Extra Usage</b>\n{sep()}\n\n"
        f"Ran out of weekly scans? Buy bonus usage!\n\n"
        f"✅ <b>No expiry</b> — carries over forever\n"
        f"✅ <b>Stackable</b> — buy multiple times\n"
        f"✅ <b>Instant</b> — available immediately\n\n"
        f"<b>Scan Packs:</b>\n"
        f"  🔬 10 scans — ⭐75\n"
        f"  🔬 50 scans — ⭐300\n"
        f"  🔬 100 scans — ⭐500\n\n"
        f"<b>AI Message Packs:</b>\n"
        f"  🤖 10 messages — ⭐100\n"
        f"  🤖 50 messages — ⭐400\n"
        f"  🤖 100 messages — ⭐700\n\n"
        f"💳 Pay with Telegram Stars ⭐"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=topup_kb())


async def cmd_scamschool(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        f"📚 <b>Scam School</b>\n{sep()}\n\n"
        f"Learn how to spot crypto scams <b>before</b> you get rugged.\n\n"
        f'Free education from <a href="{WEBSITE_URL}">RugMunch Intelligence</a>.\n\n'
        f"Select a topic below 👇"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=scamschool_kb(),
        disable_web_page_preview=True,
    )


async def cmd_refer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = db.get_or_create_user(user.id, user.username, user.first_name)
    ref = u.get("referral_code", "UNKNOWN")
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{ref}"
    conn = db.get_db()
    count = conn.execute(
        "SELECT COUNT(*) as c FROM users WHERE referred_by = ?", (ref,)
    ).fetchone()["c"]
    conn.close()
    milestones = {5: "🥉", 10: "🥈", 25: "🥇", 50: "💎", 100: "👑"}
    next_ms = next((k for k in sorted(milestones.keys()) if k > count), None)
    ms_text = (
        f"\n🎯 Next milestone: {milestones[next_ms]} {next_ms} referrals"
        if next_ms
        else "\n👑 You've hit all milestones!"
    )
    text = (
        f"🤝 <b>Refer & Earn</b>\n{sep()}\n\n"
        f"Invite friends and <b>both get 5 bonus scans!</b>\n\n"
        f"🔗 <b>Your Link:</b>\n<code>{link}</code>\n\n"
        f"📊 <b>Your Stats:</b>\n"
        f"  Friends Referred: <b>{count}</b>\n"
        f"  Bonus Earned: <b>{count * 5}</b> scans{ms_text}\n\n"
        f"<b>How it works:</b>\n"
        f"  1. Share your referral link\n"
        f"  2. Friend starts the bot via your link\n"
        f"  3. Both of you get 5 bonus scans instantly\n"
        f"  4. No limits — refer as many as you want!"
        f"{footer_links()}"
    )
    share_text = quote(
        f"Check out RugMunch Intelligence — the best crypto scam detector on Telegram! 🛡️\n\n{link}"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📤 Share Link",
                        url=f"https://t.me/share/url?url={link}&text={share_text}",
                    )
                ],
                [InlineKeyboardButton("🌐 rugmunch.io", url=WEBSITE_URL)],
                [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
            ]
        ),
        disable_web_page_preview=True,
    )


async def cmd_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    items = db.get_watchlist(user.id)
    if not items:
        await update.message.reply_text(
            "👀 <b>Watchlist Empty</b>\n\n"
            "Add tokens to track:\n"
            "  /watch <code>address</code> [chain]\n\n"
            "<i>Scout: 10 slots | Hunter: 50 | Pro: unlimited</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return
    tier = db.get_user_tier(user.id)
    text = f"👀 <b>Watchlist</b> ({TIERS[tier]['emoji']} {TIERS[tier]['name']})\n{sep()}\n\n"
    for i, w in enumerate(items, 1):
        alert = ""
        if w.get("alert_price"):
            arrow = "↑" if w.get("alert_direction") == "above" else "↓"
            alert = f" 🔔{arrow}{fmt_number(w['alert_price'])}"
        text += f"{i}. <b>{w.get('symbol') or '???'}</b> <code>{short_addr(w['token_address'])}</code> ({w['chain']}){alert}\n"
    text += f"\n{footer_links()}"
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
        disable_web_page_preview=True,
    )


async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text(
            "👀 Usage: /watch <code>address</code> [chain]", parse_mode=ParseMode.HTML
        )
        return
    addr = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else detect_chain(addr)
    symbol = ctx.args[2] if len(ctx.args) > 2 else None
    ok = db.add_watchlist(user.id, addr, chain, symbol)
    if ok:
        await update.message.reply_text(
            f"✅ Added <code>{short_addr(addr)}</code> to watchlist.\n/watchlist to view all.",
            parse_mode=ParseMode.HTML,
        )
    else:
        tier = db.get_user_tier(user.id)
        if tier == "free":
            await update.message.reply_text(
                "❌ Watchlists require <b>Scout tier</b> or higher.\n\n"
                "🔍 <b>Scout ($29.99/mo)</b> — 10 watchlist slots\n"
                "🎯 <b>Hunter ($49.99/mo)</b> — 50 slots + alerts\n"
                "👑 <b>Pro ($99.99/mo)</b> — unlimited",
                parse_mode=ParseMode.HTML,
                reply_markup=pricing_kb(),
            )
        else:
            await update.message.reply_text(
                "❌ Could not add. Limit reached or already added.",
                parse_mode=ParseMode.HTML,
            )


async def cmd_unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text(
            "Usage: /unwatch <code>address</code>", parse_mode=ParseMode.HTML
        )
        return
    addr = ctx.args[0]
    ok = db.remove_watchlist(user.id, addr)
    if ok:
        await update.message.reply_text(
            f"✅ Removed <code>{short_addr(addr)}</code> from watchlist.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            "❌ Token not found in your watchlist.", parse_mode=ParseMode.HTML
        )


async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tier = db.get_user_tier(user.id)
    if tier in ("free",):
        await update.message.reply_text(
            f"🔔 <b>Price Alerts</b>\n\n"
            f"Get notified when tokens hit your target price.\n\n"
            f"⚠️ <b>Requires Scout tier</b> or higher.\n"
            f"You're on {TIERS[tier]['emoji']} {TIERS[tier]['name']}.\n\n"
            f"🎯 <b>Hunter+</b> gets real-time exchange flow alerts too!",
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return
    items = db.get_watchlist(user.id)
    alerts = [w for w in items if w.get("alert_price")]
    if not alerts:
        await update.message.reply_text(
            "🔔 <b>Price Alerts</b>\n\nNo alerts set yet. Add a token with /watch first.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return
    text = f"🔔 <b>Active Alerts</b>\n{sep()}\n\n"
    for a in alerts:
        arrow = "↑" if a.get("alert_direction") == "above" else "↓"
        text += f"• <b>{a.get('symbol', '???')}</b> {arrow} {fmt_number(a['alert_price'])}\n"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=back_kb())


# ══════════════════════════════════════════════
# OWNER COMMANDS
# ══════════════════════════════════════════════
async def cmd_admin_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    conn = db.get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    tiers = conn.execute("SELECT tier, COUNT(*) as c FROM users GROUP BY tier").fetchall()
    scans_today = (
        conn.execute(
            "SELECT SUM(scans) as c FROM weekly_usage WHERE week_start = ?",
            (db._current_week_start(),),
        ).fetchone()["c"]
        or 0
    )
    total_scans = conn.execute("SELECT SUM(total_scans) as c FROM users").fetchone()["c"] or 0
    banned = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_banned = 1").fetchone()["c"]
    conn.close()
    tier_str = "\n".join(f"  {t['tier']}: {t['c']}" for t in tiers)
    await update.message.reply_text(
        f"📊 <b>Bot Stats</b>\n{thin_sep()}\n"
        f"👥 Total Users: {total}\n🚫 Banned: {banned}\n\n"
        f"📈 <b>Tiers:</b>\n{tier_str}\n\n"
        f"🔍 This Week: {scans_today}\n🔍 All-Time: {total_scans}",
        parse_mode=ParseMode.HTML,
    )


async def cmd_admin_set_tier(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /admin_set_tier <user_id> <tier> [days]")
        return
    uid, tier = int(ctx.args[0]), ctx.args[1].lower()
    days = int(ctx.args[2]) if len(ctx.args) > 2 else 30
    if tier not in TIERS:
        await update.message.reply_text(f"Invalid tier. Options: {', '.join(TIERS.keys())}")
        return
    db.set_user_tier(uid, tier, days)
    await update.message.reply_text(f"✅ Set user {uid} to {tier} ({days} days)")


async def cmd_admin_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /admin_broadcast <message>")
        return
    msg_text = " ".join(ctx.args).replace("\\n", "\n")
    conn = db.get_db()
    users = conn.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
    conn.close()
    sent = failed = 0
    for u in users:
        try:
            await ctx.bot.send_message(u["user_id"], msg_text, parse_mode=ParseMode.HTML)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await update.message.reply_text(f"📢 Broadcast complete.\n✅ Sent: {sent}\n❌ Failed: {failed}")


async def cmd_admin_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /admin_ban <user_id> [0|1]")
        return
    uid, state = int(ctx.args[0]), int(ctx.args[1]) if len(ctx.args) > 1 else 1
    conn = db.get_db()
    conn.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (state, uid))
    conn.commit()
    conn.close()
    await update.message.reply_text(
        f"{'🚫' if state else '✅'} User {uid} {'banned' if state else 'unbanned'}."
    )


# ══════════════════════════════════════════════
# MESSAGE HANDLER (Auto-detect + AI Chat)
# ══════════════════════════════════════════════
async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    u = db.get_or_create_user(user.id, user.username, user.first_name)
    if u.get("is_banned"):
        return
    if check_spam(user.id):
        return

    # Auto-detect contract address
    if is_evm(text) or is_sol(text):
        ctx.args = [text]
        await cmd_scan(update, ctx)
        return

    # Token symbol detection ($TOKEN)
    token_match = TOKEN_RE.search(text)
    if token_match:
        symbol = token_match.group(1).upper()
        await update.message.chat.send_action("typing")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{_DEXSCREENER}/search/?q={symbol}")
                if r.status_code == 200:
                    pairs = r.json().get("pairs", [])
                    if pairs:
                        p = pairs[0]
                        addr = p.get("baseToken", {}).get("address", "")
                        chain = p.get("chainId", "")
                        price = float(p.get("priceUsd", 0))
                        h24 = p.get("priceChange", {}).get("h24") or 0
                        vol = float(p.get("volume", {}).get("h24") or 0)
                        mcap = float(p.get("marketCap") or 0)
                        name = p.get("baseToken", {}).get("name", symbol)
                        quick_text = (
                            f"🪙 <b>{name}</b> ({symbol}) {fmt_chain(chain)}\n"
                            f"<code>{addr}</code>\n\n"
                            f"💰 {fmt_number(price)} | 🔄 24h: {fmt_pct(h24)}\n"
                            f"📈 MCap: {fmt_number(mcap)} | 💧 Vol: {fmt_number(vol)}"
                            f"{footer_links()}"
                        )
                        if addr:
                            await update.message.reply_text(
                                quick_text,
                                parse_mode=ParseMode.HTML,
                                reply_markup=InlineKeyboardMarkup(
                                    [
                                        [
                                            InlineKeyboardButton(
                                                "🔍 Full Scan",
                                                callback_data=f"scan_{addr}_{chain}",
                                            )
                                        ],
                                        [web_scan_button(addr, chain)],
                                    ]
                                ),
                                disable_web_page_preview=True,
                            )
                            return
        except Exception:
            pass

    # Natural language address extraction
    evm_match = EVM_RE.search(text)
    sol_match = SOL_RE.search(text)
    if evm_match:
        ctx.args = [evm_match.group()]
        await cmd_scan(update, ctx)
        return
    if sol_match:
        ctx.args = [sol_match.group()]
        await cmd_scan(update, ctx)
        return

    # AI Chat
    if not is_owner(user.id):
        allowed, _used, _limit = db.check_rate_limit(user.id, "ai_msg")
        if not allowed:
            await update.message.reply_text(
                paywall_text(user.id, "ai_msg"),
                parse_mode=ParseMode.HTML,
                reply_markup=paywall_kb(),
            )
            return

    await update.message.chat.send_action("typing")

    # Try RMI RAG backend (v2 search endpoint)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{BACKEND_URL}/api/v1/rag/v2/search",
                json={
                    "query": text,
                    "collection": "scam_intel",
                    "top_k": 5,
                    "min_similarity": 0.0,
                    "filters": {},
                },
                headers={"X-API-Key": RMI_AUTH_TOKEN},
            )
            if r.status_code == 200:
                data = r.json()
                hits = data.get("hits", [])
                if hits:
                    answer = hits[0].get("content", "")
                    if answer:
                        db.increment_usage(user.id, ai_msg=True)
                        await update.message.reply_text(
                            answer, parse_mode=ParseMode.HTML, reply_markup=back_kb()
                        )
                        return
    except Exception:
        pass

    # Smart fallback responses
    text_lower = text.lower()
    if any(w in text_lower for w in ["honeypot", "rug", "scam", "safe", "check"]):
        await update.message.reply_text(
            "🔍 Want me to check a token for scams?\n\n"
            "Just paste the contract address or use:\n"
            "/scan <code>address</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif any(w in text_lower for w in ["price", "how much", "worth", "pump", "dump"]):
        await update.message.reply_text(
            "📊 Looking for a token price?\n\nSend me the symbol (e.g. <code>$PEPE</code>) or contract address!",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif any(w in text_lower for w in ["help", "how", "what can", "commands"]):
        await cmd_help(update, ctx)
    else:
        db.increment_usage(user.id, ai_msg=True)
        await update.message.reply_text(
            f"🤖 I'm here to help with crypto safety!\n\n"
            f"Try:\n"
            f"• Paste a contract address → auto-scan\n"
            f"• <code>$TOKEN</code> → quick price\n"
            f"• /scan <code>address</code> → full analysis\n"
            f"• /wallet <code>address</code> → wallet check\n"
            f"• /scamschool → learn about scams\n"
            f"• /trending → hot tokens right now"
            f"{footer_links()}",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
            disable_web_page_preview=True,
        )


# ══════════════════════════════════════════════
# INLINE QUERY (Scan from any chat)
# ══════════════════════════════════════════════
async def handle_inline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.strip()
    if not query:
        return
    results = []
    if is_evm(query) or is_sol(query):
        results.append(
            InlineQueryResultArticle(
                id="scan_" + query[:10],
                title=f"🔍 Scan {short_addr(query)}",
                description="Run a full security scan on this address",
                input_message_content=InputTextMessageContent(f"/scan {query}"),
            )
        )
    if len(query) >= 2 and len(query) <= 10 and query.isalpha():
        results.append(
            InlineQueryResultArticle(
                id="sym_" + query,
                title=f"🪙 Lookup ${query.upper()}",
                description="Find token price and info",
                input_message_content=InputTextMessageContent(f"${query.upper()}"),
            )
        )
    if results:
        await update.inline_query.answer(results, cache_time=5, is_personal=True)


# ══════════════════════════════════════════════
# CALLBACK HANDLER
# ══════════════════════════════════════════════
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user
    db.get_or_create_user(user.id, user.username, user.first_name)

    if data == "menu_main":
        await query.edit_message_text(
            "🛡️ <b>RugMunch Intelligence</b>\n\nWhat would you like to do?",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
    elif data == "menu_scan":
        await query.edit_message_text(
            "🔍 <b>Scan a Token</b>\n\nSend me a contract address:\n<code>0x6982...1933</code>\n\nOr use: /scan <code>address</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_wallet":
        await query.edit_message_text(
            "👛 <b>Wallet Check</b>\n\nSend a wallet address or use:\n/wallet <code>address</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_ta":
        await query.edit_message_text(
            "📊 <b>Technical Analysis</b>\n\nUsage: /ta <code>address</code>\n\nAnalyzes momentum, volume, buy/sell pressure.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_alerts":
        await query.edit_message_text(
            "🔔 <b>Price Alerts</b>\n\nUse /alerts to manage.\n\n<i>Scout+ tiers only.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_account":
        tier = db.get_user_tier(user.id)
        tc = TIERS.get(tier, TIERS["free"])
        scans, ai = db.get_weekly_usage(user.id)
        u = db.get_user_stats(user.id)
        text = (
            f"👤 <b>Account</b>\n{thin_sep()}\n"
            f"Tier: {tc['emoji']} {tc['name']}\n"
            f"Scans: {scans}/{tc.get('scans_per_week', 25)}\n"
            f"AI: {ai}/{tc.get('ai_msgs_per_week', 15)}\n"
            f"Bonus Scans: {u.get('bonus_scans', 0)}\n"
            f"Bonus AI: {u.get('bonus_ai_msgs', 0)}\n"
            f"Stars: {u.get('stars_balance', 0)}⭐\n"
            f"Watchlist: {u.get('watchlist_count', 0)}"
        )
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("⭐ Upgrade", callback_data="menu_pricing"),
                        InlineKeyboardButton("🔋 Top Up", callback_data="menu_topup"),
                    ],
                    [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
                ]
            ),
        )
    elif data == "menu_pricing":
        await query.edit_message_text(
            "⭐ <b>Upgrade Plan</b>\n\nSelect a tier:",
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
    elif data == "menu_topup":
        await query.edit_message_text(
            "🔋 <b>Top Up</b>\n\nBuy extra usage (no expiry):",
            parse_mode=ParseMode.HTML,
            reply_markup=topup_kb(),
        )
    elif data == "menu_scamschool":
        await query.edit_message_text(
            "📚 <b>Scam School</b>\n\nSelect a topic:",
            parse_mode=ParseMode.HTML,
            reply_markup=scamschool_kb(),
        )
    elif data == "menu_trending":
        await query.edit_message_text("🔥 <b>Fetching trending...</b>", parse_mode=ParseMode.HTML)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{_DEXSCREENER}/search/?q=trending")
                if r.status_code == 200:
                    pairs = r.json().get("pairs", [])[:6]
                    lines = ["🔥 <b>Trending</b>", thin_sep()]
                    for _i, p in enumerate(pairs, 1):
                        name = p.get("baseToken", {}).get("name", "???")[:12]
                        sym = p.get("baseToken", {}).get("symbol", "???")
                        h24 = p.get("priceChange", {}).get("h24") or 0
                        addr = p.get("baseToken", {}).get("address", "")
                        emoji = "🟢" if h24 >= 0 else "🔴"
                        lines.append(
                            f"{emoji} <b>{name}</b> ({sym}) {fmt_pct(h24)}\n   <code>{short_addr(addr)}</code>"
                        )
                    await query.edit_message_text(
                        "\n".join(lines),
                        parse_mode=ParseMode.HTML,
                        reply_markup=back_kb(),
                    )
                else:
                    await query.edit_message_text(
                        "❌ Could not fetch trending data.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=back_kb(),
                    )
        except Exception:
            await query.edit_message_text(
                "❌ Trending unavailable.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data == "menu_watchlist":
        items = db.get_watchlist(user.id)
        if not items:
            await query.edit_message_text(
                "👀 Watchlist is empty.\nUse /watch <code>address</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
        else:
            text = "👀 <b>Watchlist</b>\n\n" + "\n".join(
                f"• {w.get('symbol', '???')} <code>{short_addr(w['token_address'])}</code>"
                for w in items
            )
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_kb())
    elif data == "menu_refer":
        u = db.get_or_create_user(user.id)
        link = f"https://t.me/{BOT_USERNAME}?start=ref_{u.get('referral_code', '')}"
        await query.edit_message_text(
            f"🤝 <b>Refer Friends</b>\n\nShare your link:\n<code>{link}</code>\n\nBoth get 5 bonus scans!",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data.startswith("scam_"):
        topic_key = data[5:]
        topic = SCAM_SCHOOL_TOPICS.get(topic_key)
        if topic:
            await query.edit_message_text(
                topic["content"],
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=scamschool_kb(),
            )
    elif data.startswith("scan_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            await query.edit_message_text(
                f"🔍 Use /scan {parts[1]} to run a full scan.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("watch_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            ok = db.add_watchlist(user.id, addr, chain)
            await query.answer(
                "Added to watchlist!" if ok else "Could not add (limit reached?)",
                show_alert=True,
            )
    elif data.startswith("sub_"):
        tier_key = data[4:]
        tier = TIERS.get(tier_key)
        if tier:
            await query.edit_message_text(
                f"💳 <b>Subscribe to {tier['name']}</b>\n\n"
                f"${tier['price_monthly']}/month\n"
                f"or ⭐{tier.get('price_stars', 0)} Stars\n\n"
                f"<i>Payment integration coming next update.\n"
                f"Contact {SUPPORT_EMAIL} for now.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=pricing_kb(),
            )
    elif data.startswith("topup_"):
        pack_key = data[6:]
        pack = TOP_UP_PACKS.get(pack_key)
        if pack:
            u = db.get_or_create_user(user.id)
            if u.get("stars_balance", 0) >= pack["stars"]:
                ok = db.apply_top_up(user.id, pack["type"], pack["amount"], pack["stars"])
                if ok:
                    await query.edit_message_text(
                        f"✅ <b>Top Up Applied!</b>\n\n"
                        f"+{pack['amount']} bonus {pack['type'].replace('_', ' ')}\n"
                        f"Cost: ⭐{pack['stars']}\n\n"
                        f"These never expire!",
                        parse_mode=ParseMode.HTML,
                        reply_markup=topup_kb(),
                    )
                else:
                    await query.answer("Transaction failed.", show_alert=True)
            else:
                await query.edit_message_text(
                    f"❌ <b>Not Enough Stars</b>\n\n"
                    f"You need ⭐{pack['stars']} but have ⭐{u.get('stars_balance', 0)}.\n\n"
                    f"Buy Stars via Telegram or earn through referrals!",
                    parse_mode=ParseMode.HTML,
                    reply_markup=topup_kb(),
                )


# ══════════════════════════════════════════════
# PAYMENT HANDLER
# ══════════════════════════════════════════════
async def precheckout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)


async def successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a successful Telegram Stars payment.

    Credits the user's star balance and confirms the transaction.

    Args:
        update: The incoming Telegram update (contains successful_payment).
        ctx: The application context.
    """
    payment = update.message.successful_payment
    user = update.effective_user
    db.add_stars(user.id, payment.total_amount, "payment", payment.telegram_payment_charge_id)
    await update.message.reply_text(
        f"✅ <b>Payment Received!</b>\n\n"
        f"⭐ {payment.total_amount} Stars added to your balance.\n"
        f"Use /topup to spend them.",
        parse_mode=ParseMode.HTML,
    )


# ══════════════════════════════════════════════
# GLOBAL ERROR HANDLER
# ══════════════════════════════════════════════
async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler for unhandled exceptions.

    Logs the error with stack trace and notifies the user if possible.

    Args:
        update: The update that caused the error (may be None).
        ctx: The application context containing the error.
    """
    logger.error(f"Exception: {ctx.error}", exc_info=ctx.error)
    if isinstance(update, Update) and update.effective_message:
        with contextlib.suppress(Exception):
            await update.effective_message.reply_text(
                "⚠️ Something went wrong. Please try again.\nIf the issue persists, contact support.",
                parse_mode=ParseMode.HTML,
            )


# ══════════════════════════════════════════════
# BOT PROFILE SETUP (BotFather API)
# ══════════════════════════════════════════════
async def setup_bot_profile(app: Application) -> None:
    """Set bot description, about text, and command list via BotFather API.

    Configures the bot's public profile including description text,
    short description, and the full command list shown in Telegram UI.

    Args:
        app: The Telegram application instance.
    """
    bot = app.bot
    try:
        await bot.set_my_description(description=BOT_DESCRIPTION)
        logger.info("Bot description set")
    except Exception as e:
        logger.warning(f"Could not set description: {e}")
    try:
        await bot.set_my_short_description(short_description=BOT_SHORT_DESCRIPTION)
        logger.info("Bot short description set")
    except Exception as e:
        logger.warning(f"Could not set short description: {e}")
    try:
        commands = [
            BotCommand("start", "Welcome & quick start guide"),
            BotCommand("help", "Full command reference"),
            BotCommand("status", "Bot health & uptime"),
            BotCommand("scan", "Scan token for scams & risks"),
            BotCommand("wallet", "Wallet forensics & analysis"),
            BotCommand("ta", "Technical analysis & signals"),
            BotCommand("compare", "Compare two tokens side-by-side"),
            BotCommand("quick", "Quick token price check"),
            BotCommand("trending", "Hot tokens right now"),
            BotCommand("rugcheck", "Rug pull red flag checklist"),
            BotCommand("watch", "Add token to watchlist"),
            BotCommand("watchlist", "View your watchlist"),
            BotCommand("alerts", "Price & activity alerts"),
            BotCommand("account", "Your dashboard & usage"),
            BotCommand("pricing", "Subscription plans"),
            BotCommand("topup", "Buy extra scans (no expiry)"),
            BotCommand("refer", "Invite friends, earn scans"),
            BotCommand("scamschool", "Learn about crypto scams"),
            BotCommand("news", "Latest crypto news"),
        ]
        await bot.set_my_commands(commands)
        logger.info("Bot commands registered")
    except Exception as e:
        logger.warning(f"Could not set commands: {e}")


# ══════════════════════════════════════════════
# SCHEDULED JOBS
# ══════════════════════════════════════════════
async def daily_scam_tip(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Post a daily scam awareness tip to the main channel.

    Selects a random topic from the Scam School curriculum and sends
    an educational message with links to learn more.

    Args:
        ctx: The application context (provides bot reference).
    """
    if not CHANNELS.get("main"):
        return
    topic_keys = list(SCAM_SCHOOL_TOPICS.keys())
    topic_key = secrets.choice(topic_keys)
    topic = SCAM_SCHOOL_TOPICS[topic_key]
    text = (
        f"🛡️ <b>Daily Scam Tip</b>\n{sep()}\n\n"
        f"{topic['content']}\n\n"
        f"📚 Learn more: /scamschool\n"
        f"🔍 Scan tokens: @RugMunchBot\n"
        f'🌐 <a href="{WEBSITE_URL}">rugmunch.io</a>'
    )
    try:
        await ctx.bot.send_message(
            CHANNELS["main"],
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"Daily tip failed: {e}")


# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════


async def cmd_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show payment methods and subscription plans.

    Displays available subscription tiers with pricing and accepted
    payment methods (crypto, card, Telegram Stars).

    Args:
        update: The incoming Telegram update.
        ctx: The application context.
    """
    try:
        # Inline subscription plans
        SUBSCRIPTION_PLANS = {
            "free": {
                "name": "Free",
                "pricing": {"usdc": {"monthly": 0}},
                "credits_per_day": 5,
            },
            "basic": {
                "name": "Basic",
                "pricing": {"usdc": {"monthly": 9}},
                "credits_per_day": 50,
            },
            "pro": {
                "name": "Pro",
                "pricing": {"usdc": {"monthly": 29}},
                "credits_per_day": 200,
            },
            "enterprise": {
                "name": "Enterprise",
                "pricing": {"usdc": {"monthly": 99}},
                "credits_per_day": 1000,
            },
        }
        text = "<b>RMI Subscription Plans</b>\n\n"
        for plan in SUBSCRIPTION_PLANS.values():
            price = plan["pricing"]["usdc"]["monthly"]
            text += "<b>" + plan["name"] + "</b> - $" + str(price) + "/mo\n"
            text += "  " + str(plan["credits_per_day"]) + " credits/day\n\n"
        text += "\n<b>Payment Methods:</b>\n"
        text += "⛓ Crypto (any token, any chain): USDC, ETH, SOL, USDT, TON\n"
        text += "💳 Card / Apple Pay / Google Pay / PayPal via rugmunch.io\n"
        text += "⭐ Telegram Stars\n\n"
        text += "Visit rugmunch.io/pricing to subscribe or use /buy [plan]."
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"cmd_buy_error: {e}")
        await update.message.reply_text("Visit rugmunch.io/pricing")


def main():
    if not BOT_TOKEN:
        logger.error("RUGMUNCH_BOT_TOKEN not set!")
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).build()

    # ── Commands ──
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("wallet", cmd_wallet))
    app.add_handler(CommandHandler("ta", cmd_ta))
    app.add_handler(CommandHandler("compare", cmd_compare))
    app.add_handler(CommandHandler("quick", cmd_quick))
    app.add_handler(CommandHandler("rugcheck", cmd_rugcheck))
    app.add_handler(CommandHandler("trending", cmd_trending))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("account", cmd_account))
    app.add_handler(CommandHandler("pricing", cmd_pricing))
    app.add_handler(CommandHandler("topup", cmd_topup))
    app.add_handler(CommandHandler("scamschool", cmd_scamschool))
    app.add_handler(CommandHandler("refer", cmd_refer))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("buy", cmd_buy))
    app.add_handler(CommandHandler("subscribe", cmd_buy))

    # ── Owner Commands ──
    app.add_handler(CommandHandler("admin_stats", cmd_admin_stats))
    app.add_handler(CommandHandler("admin_set_tier", cmd_admin_set_tier))
    app.add_handler(CommandHandler("admin_broadcast", cmd_admin_broadcast))
    app.add_handler(CommandHandler("admin_ban", cmd_admin_ban))

    # ── Inline Query ──
    app.add_handler(InlineQueryHandler(handle_inline))

    # ── Callbacks & Messages ──
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # ── Payments ──
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    # ── Error Handler ──
    app.add_error_handler(error_handler)

    # ── Scheduled Jobs ──
    job_queue = app.job_queue
    job_queue.run_daily(daily_scam_tip, dtime(hour=14, minute=0, tzinfo=UTC))

    # ── Startup: set bot profile ──
    app.post_init = setup_bot_profile

    logger.info("🛡️ CryptoRugMunch Bot v6 starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
