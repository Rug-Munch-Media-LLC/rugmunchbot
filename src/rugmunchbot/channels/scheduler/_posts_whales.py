"""Whale-alerts channel — multi-chain whale moves from the firehose corpus.

Reads recent ``whale_transfers`` docs from Qdrant v2 (the firehose's 18-source
ingestion), filters to a fresh window, dedupes against a Redis seen-set, and
formats degen-grade alert lines: chain badge, amount, direction when known.

Cadence: 30 min via scheduler registration (``channel_whale_alerts``).
Fail-open at every stage — a dead qdrant never breaks the tick.
"""

from __future__ import annotations

import os
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


def _fmt_amount(meta: dict[str, Any]) -> str:
    if meta.get("amount_usd"):
        return f"${meta['amount_usd'] / 1e6:.1f}M {meta.get('token', 'stables')}"
    if meta.get("amount_btc"):
        return f"{meta['amount_btc']:.1f} BTC"
    if meta.get("amount_sol"):
        return f"{meta['amount_sol']:,.0f} SOL"
    if meta.get("amount_native"):
        return f"{meta['amount_native']:,.0f} {meta.get('symbol', '')}"
    return "sizeable move"


def _fmt_doc(point_id: str, payload: dict[str, Any]) -> str | None:
    meta = payload.get("metadata") or {}
    chain = str(meta.get("chain", "?")).lower()
    badge = _CHAIN_EMOJI.get(chain, "🌐")
    mtype = meta.get("type", "large_transfer")
    amount = _fmt_amount(meta)

    if mtype == "positioning":
        # HL positioning docs are summaries — post as-is, trimmed.
        content = str(payload.get("content", ""))[:280]
        return f"⚡ <b>Hyperliquid whales</b>\n{content}"
    if mtype == "stablecoin_whale":
        to = str(meta.get("to", ""))
        to_short = f" → <code>{to[:8]}…{to[-6:]}</code>" if len(to) > 16 else ""
        return f"{badge} <b>Stablecoin whale</b> ({chain})\n{amount} moved{to_short}"
    frm = str(meta.get("from", ""))
    to = str(meta.get("to", ""))
    route = ""
    if len(frm) > 16 and len(to) > 16:
        route = f"\n<code>{frm[:8]}…{frm[-6:]}</code> → <code>{to[:8]}…{to[-6:]}</code>"
    return f"{badge} <b>Whale transfer</b> ({chain})\n{amount}{route}"


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
            host=os.getenv("REDIS_HOST", "rmi-redis"),
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

    if not lines:
        if rclient:
            await rclient.aclose()
        return None

    header = f"🐋 <b>WHALE WATCH</b> — last {FRESH_HOURS:.0f}h"
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
