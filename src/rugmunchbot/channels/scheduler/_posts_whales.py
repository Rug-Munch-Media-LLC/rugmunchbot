"""Whale-alerts channel — multi-chain whale moves from the firehose corpus.

Reads recent ``whale_transfers`` docs from Qdrant v2 (the firehose's 18-source
ingestion), filters to a fresh window, dedupes against a Redis seen-set, and
formats degen-grade alert lines: chain badge, amount, wallet route when known.

WHALE-SIGNAL CONTRACT (2026-08-30 — fix-whale-signal): every whale alert MUST
carry the actual signal — a wallet address (from/to), the token + chain, the
amount (USD + native when known), a timestamp, and an explorer/tx source link.
A transfer payload with NO wallet address is DROPPED (``None``): a whale alert
without the wallet is worthless noise. Positioning/HL summaries are a separate
genre (market analysis, not a transfer) and render their content + timestamp
as-is.

Cadence: 30 min via scheduler registration (``channel_whale_alerts``).
Fail-open at every stage — a dead qdrant never breaks the tick.
"""

from __future__ import annotations

import html
import os
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")
COLLECTION = "whale_transfers"
FRESH_HOURS = float(os.getenv("WHALE_ALERT_FRESH_H", "6"))
MAX_ALERTS = 5
_SEEN_KEY = "whale_alerts:seen_ids"

_CHAIN_EMOJI = {
    "ethereum": "⟠",
    "base": "🔵",
    "arbitrum": "🔷",
    "bsc": "🟡",
    "polygon": "🟣",
    "avalanche": "🔺",
    "bitcoin": "🟠",
    "solana": "◎",
    "hyperliquid": "⚡",
}

#: Explorer base URLs per chain (tx + account) for the source-link contract.
_EXPLORER_TX: dict[str, str] = {
    "solana": "https://solscan.io/tx/",
    "ethereum": "https://etherscan.io/tx/",
    "base": "https://basescan.org/tx/",
    "arbitrum": "https://arbiscan.io/tx/",
    "bsc": "https://bscscan.com/tx/",
    "polygon": "https://polygonscan.com/tx/",
    "avalanche": "https://snowtrace.io/tx/",
}
_EXPLORER_ACCOUNT: dict[str, str] = {
    "solana": "https://solscan.io/account/",
    "ethereum": "https://etherscan.io/address/",
    "base": "https://basescan.org/address/",
    "arbitrum": "https://arbiscan.io/address/",
    "bsc": "https://bscscan.com/address/",
    "polygon": "https://polygonscan.com/address/",
    "avalanche": "https://snowtrace.io/address/",
}


def _fmt_amount(meta: dict[str, Any]) -> str:
    """Full amount + token line: USD value + native amount together.

    A whale doc can report BOTH a USD estimate and the native token amount;
    combine every field the metadata actually carries into one line so the
    reader sees the size in both terms (e.g. ``$12.5M USDT · 4,000 SOL``).
    Empty → the honest fallback phrase. Pure — never raises.
    """
    parts: list[str] = []
    usd = meta.get("amount_usd")
    if usd:
        token = str(meta.get("token") or "").strip()
        with suppress(TypeError, ValueError):
            m = f"${float(usd) / 1e6:.1f}M"
            parts.append(f"{m} {token}".rstrip() if token else m)
    if meta.get("amount_btc"):
        with suppress(TypeError, ValueError):
            parts.append(f"{float(meta['amount_btc']):.1f} BTC")
    if meta.get("amount_sol"):
        with suppress(TypeError, ValueError):
            parts.append(f"{float(meta['amount_sol']):,.0f} SOL")
    if meta.get("amount_native"):
        sym = str(meta.get("symbol") or "").strip()
        with suppress(TypeError, ValueError):
            raw = f"{float(meta['amount_native']):,.0f} {sym}".rstrip()
            parts.append(raw)
    return " · ".join(parts) if parts else "sizeable move"


def _explorer_tx_url(chain: str, tx_hash: str) -> str:
    """Per-chain transaction explorer URL ("" when chain/tx unknown)."""
    tx_hash = (tx_hash or "").strip()
    if not tx_hash:
        return ""
    prefix = _EXPLORER_TX.get((chain or "").lower())
    return f"{prefix}{tx_hash}" if prefix else ""


def _explorer_account_url(chain: str, address: str) -> str:
    """Per-chain wallet/account explorer URL ("" when chain/addr unknown)."""
    address = (address or "").strip()
    if not address:
        return ""
    prefix = _EXPLORER_ACCOUNT.get((chain or "").lower())
    return f"{prefix}{address}" if prefix else ""


def _fmt_time(meta: dict[str, Any]) -> str:
    """Readable timestamp from the doc's ``published`` field ("" when absent)."""
    raw = str(meta.get("published") or "").strip()
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return f"🕐 {dt.strftime('%b %d, %H:%M UTC')}"
    except ValueError:
        return f"🕐 {raw}"


def _source_link(meta: dict[str, Any]) -> str:
    """One explorer/tx source link for a whale doc ("" when unknown).

    Prefers the transaction hash (solscan/etherscan tx link); falls back to
    the wallet account explorer when we only have an address. Pure.
    """
    chain = str(meta.get("chain") or "").lower()
    tx = str(meta.get("tx_hash") or meta.get("hash") or meta.get("transaction_hash") or "").strip()
    if tx:
        url = _explorer_tx_url(chain, tx)
        if url:
            return f'🔗 <a href="{html.escape(url, quote=True)}">View tx</a>'
    addr = str(meta.get("from") or meta.get("to") or "").strip()
    if addr:
        url = _explorer_account_url(chain, addr)
        if url:
            return f'🔗 <a href="{html.escape(url, quote=True)}">Explorer</a>'
    return ""


def _wallet_label(meta: dict[str, Any]) -> str:
    """Human wallet label from the doc metadata ("" when unlabeled).

    Checks the common label fields the firehose/enrichment layers write
    (``label`` / ``wallet_label`` / ``entity`` / ``owner``). Pure.
    """
    for key in ("label", "wallet_label", "entity", "owner"):
        val = str(meta.get(key) or "").strip()
        if val:
            return val
    return ""


def _wallet_context(meta: dict[str, Any]) -> str:
    """One-line wallet context from the doc metadata ("" when absent).

    Reads ``context`` / ``history`` / ``note`` — the enrichment fields the
    backend whale feed (labels / accumulating-vs-distributing / smart-money)
    writes into the firehose payload. Pure.
    """
    for key in ("context", "history", "note"):
        val = str(meta.get(key) or "").strip()
        if val:
            return val[:160]
    return ""


def _deep_dive_link(meta: dict[str, Any]) -> str:
    """rugmunch.io full-analysis link for the wallet ("" when no wallet).

    Deep-dive contract (2026-08-31): every whale alert links to the wallet's
    full analysis on rugmunch.io so the channel funnels to the paid product.
    Pure.
    """
    addr = str(meta.get("from") or meta.get("to") or "").strip()
    if not addr:
        return ""
    chain = str(meta.get("chain") or "").strip().lower()
    url = f"https://rugmunch.io/wallet/{addr}"
    if chain and chain != "?":
        url += f"?chain={chain}"
    return f'🔍 <a href="{html.escape(url, quote=True)}">Full analysis</a>'


def _fmt_doc(point_id: str, payload: dict[str, Any]) -> str | None:
    """One whale-alert line, guaranteed to carry the actual signal.

    Every transfer alert renders: the wallet address(es) (from/to, full and
    copy-able), the token + chain, the amount (USD + native when known), a
    timestamp, and an explorer/tx source link. A transfer payload with NO
    wallet address is DROPPED (returns ``None``) — a whale alert without the
    wallet is worthless noise. Positioning/HL summaries are market-analysis
    (not a transfer) and render their content + timestamp as-is. Pure —
    never raises.
    """
    meta = payload.get("metadata") or {}
    chain = str(meta.get("chain", "?")).lower()
    badge = _CHAIN_EMOJI.get(chain, "🌐")
    mtype = meta.get("type", "large_transfer")

    if mtype == "positioning":
        # HL positioning docs are summaries — post content + timestamp as-is.
        content = str(payload.get("content", ""))[:280]
        ts = _fmt_time(meta)
        if ts:
            return f"⚡ <b>Hyperliquid whales</b>\n{content}\n{ts}"
        return f"⚡ <b>Hyperliquid whales</b>\n{content}"

    frm = str(meta.get("from", "") or "").strip()
    to = str(meta.get("to", "") or "").strip()
    # Whale-signal gate — a transfer without a wallet on either end is dropped.
    if not (frm or to):
        return None

    amount = _fmt_amount(meta)
    label = "Stablecoin whale" if mtype == "stablecoin_whale" else "Whale transfer"
    wallet_label = _wallet_label(meta)
    lines = [
        f"{badge} <b>{label}</b> ({chain})"
        + (f" — {html.escape(wallet_label)}" if wallet_label else ""),
        amount,
    ]

    # Wallet route (full, copy-able addresses — the actual signal).
    if frm and to:
        lines.append(f"👛 <code>{html.escape(frm)}</code> → <code>{html.escape(to)}</code>")
    elif to:
        lines.append(f"👛 → <code>{html.escape(to)}</code>")
    elif frm:
        lines.append(f"👛 <code>{html.escape(frm)}</code> →")

    # Wallet context (history / accumulating-vs-distributing / smart money).
    ctx = _wallet_context(meta)
    if ctx:
        lines.append(f"📊 {html.escape(ctx)}")

    ts = _fmt_time(meta)
    if ts:
        lines.append(ts)
    link = _source_link(meta)
    if link:
        lines.append(link)
    deep = _deep_dive_link(meta)
    if deep:
        lines.append(deep)
    return "\n".join(lines)


async def _fetch_webhook_whales(limit: int = 8) -> list[str]:
    """Whale-move webhook events → formatted lines (second source).

    The alerts gate (channels.content) EXCLUDES whale_move/token_transfer/
    transfer from @rmialerts by doctrine — those events land here instead:
    they're exactly the whales-lane genre. Every line carries the wallet
    route, token, amount, timestamp and a source link; a webhook event with
    no wallet on either end is DROPPED (no wallet-less noise). Fail-open to [].
    """
    try:
        from rugmunchbot._vendor.alert_pipeline import get_recent_alerts
        from rugmunchbot.channels.content import WHALE_EVENT_TYPES

        entries = await get_recent_alerts(limit=limit * 2, severity="")
    except Exception as exc:
        logger.debug("whale_alerts:webhook_source_unavailable err=%s", str(exc)[:120])
        return []

    lines: list[str] = []
    for entry in entries:
        event_type = str(entry.get("event_type") or "").strip()
        if event_type not in WHALE_EVENT_TYPES:
            continue
        chain = str(entry.get("chain") or "").strip().lower()
        badge = _CHAIN_EMOJI.get(chain, "•")
        token = str(entry.get("token_symbol") or entry.get("token") or "tokens").strip()
        token_addr = str(entry.get("token") or entry.get("token_address") or "").strip()
        attribution = entry.get("attribution")
        attribution = attribution if isinstance(attribution, dict) else {}
        frm = str(attribution.get("from") or "").strip()
        to = str(attribution.get("to") or "").strip()
        # Whale-signal gate — drop wallet-less webhook events.
        if not (frm or to):
            continue
        amount = str(entry.get("amount_usd") or entry.get("amount") or "").strip()
        amt_s = f"${amount}" if amount.replace(".", "").replace(",", "").isdigit() else amount
        header = f"{badge} <b>{token}</b> move" + (f" ({amt_s})" if amt_s else "")
        if token_addr:
            header += f" <code>{html.escape(token_addr)}</code>"
        lines.append(header)
        if frm and to:
            lines.append(f"👛 <code>{html.escape(frm)}</code> → <code>{html.escape(to)}</code>")
        elif to:
            lines.append(f"👛 → <code>{html.escape(to)}</code>")
        elif frm:
            lines.append(f"👛 <code>{html.escape(frm)}</code> →")
        created = str(entry.get("created_at") or "").strip()
        if created:
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                lines.append(f"🕐 {dt.strftime('%b %d, %H:%M UTC')}")
            except ValueError:
                lines.append(f"🕐 {created}")
        ctx = str(entry.get("context") or entry.get("history") or "").strip()
        if ctx:
            lines.append(f"📊 {html.escape(ctx[:160])}")
        tx = str(entry.get("tx_hash") or entry.get("hash") or entry.get("id") or "").strip()
        url = _explorer_tx_url(chain, tx)
        if not url:
            url = _explorer_account_url(chain, frm or to)
        if url:
            lines.append(f'🔗 <a href="{html.escape(url, quote=True)}">Source</a>')
        deep_addr = frm or to
        if deep_addr:
            deep_url = f"https://rugmunch.io/wallet/{deep_addr}"
            if chain:
                deep_url += f"?chain={chain}"
            lines.append(f'🔍 <a href="{html.escape(deep_url, quote=True)}">Full analysis</a>')
        if len(lines) >= limit:
            break
    return lines


async def build_whale_post() -> str | None:
    """Fresh whale moves → one formatted alert post (None = nothing to send)."""
    cutoff = datetime.now(tz=UTC) - timedelta(hours=FRESH_HOURS)
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{QDRANT_URL}/collections/{COLLECTION}/points/scroll",
                json={"limit": 40, "with_payload": True},
            )
            r.raise_for_status()
            points = r.json()["result"].get("points", [])
    except Exception as exc:
        logger.warning("whale_alerts:qdrant_failed err=%s", str(exc)[:120])
        return None

    import redis.asyncio as aioredis

    rclient = None
    try:
        rclient = aioredis.Redis(
            host=os.getenv("REDIS_HOST", "rugmunchbot-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
        )
        seen = set(await rclient.smembers(_SEEN_KEY)) if rclient else set()
    except Exception as exc:
        logger.debug("whale_alerts:seen_load_failed err=%s", str(exc)[:80])
        seen = set()

    lines: list[str] = []
    new_ids: list[str] = []
    for p in points:
        pid = str(p.get("id", ""))
        payload = p.get("payload") or {}
        meta = payload.get("metadata") or {}
        pub = str(meta.get("published") or "")
        fresh = False
        if pub:
            try:
                fresh = datetime.fromisoformat(pub) >= cutoff
            except ValueError:
                fresh = False
        elif pid not in seen:
            fresh = True  # undated legacy docs: show once
        if not fresh or pid in seen:
            continue
        line = _fmt_doc(pid, payload)
        if line:
            lines.append(line)
            new_ids.append(pid)
        if len(lines) >= MAX_ALERTS:
            break

    # Second source: whale-move webhook events (excluded from alerts by
    # doctrine — this is where they belong).
    webhook_lines = await _fetch_webhook_whales()
    lines.extend(webhook_lines[: max(0, MAX_ALERTS - len(lines))])

    if not lines:
        if rclient:
            await rclient.aclose()
        return None

    now = datetime.now(UTC).strftime("%b %d, %H:%M UTC")
    header = f"🐋 <b>WHALE WATCH</b> — last {FRESH_HOURS:.0f}h\n🕐 {now}"
    post = header + "\n\n" + "\n\n".join(lines)

    if rclient and new_ids:
        try:
            pipe = rclient.pipeline()
            pipe.sadd(_SEEN_KEY, *new_ids)
            pipe.expire(_SEEN_KEY, 7 * 86400)
            await pipe.execute()
        except Exception as exc:
            logger.debug("whale_alerts:seen_save_failed err=%s", str(exc)[:80])
        finally:
            await rclient.aclose()
    return post
