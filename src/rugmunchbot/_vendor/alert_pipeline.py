# VENDORED 2026-08-17 from rmi-backend/app/alert_pipeline.py (Phase 2a — frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""
RMI Alert Pipeline - Real-time threat intelligence from scanners.
=================================================================
Feeds the Live Intel panel, WebSocket streams, and alert endpoints.

Data sources:
  - SENTINEL scanner (risk scores, scam detection)
  - GoPlus security API (honeypot, tax, proxy checks)
  - DexScreener new pairs (fresh launches to scan)
  - Our own RAG scam patterns
  - Wallet label cross-references

Alert flow:
  Scanner → alert_pipeline.push_alert() → Redis sorted set + pub/sub
  Homepage reads from /api/v1/alerts/recent
  Sidebar reads from /api/v1/alerts/count
  WebSocket streams to connected clients
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger("alert_pipeline")

REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PW = os.getenv("REDIS_PASSWORD", "")

ALERT_KEY = "rmi:alerts:recent"
ALERT_COUNT_KEY = "rmi:alerts:count:active"
ALERT_MAX = 500  # Keep last 500 alerts


async def _get_redis() -> Any:
    """Get async Redis connection."""
    import redis.asyncio as aioredis

    return aioredis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PW or None,
        decode_responses=True,
    )


async def get_active_alert_count() -> int:
    """Get count of active (unacknowledged) alerts."""
    try:
        r = await _get_redis()
        count = await r.zcard(ALERT_KEY)
        await r.close()
        return count
    except Exception as e:
        logger.debug(f"Alert count error: {e}")
        return 0


async def push_alert(
    alert_type: str,
    title: str,
    description: str = "",
    severity: str = "high",
    chain: str = "unknown",
    token: str = "",
    token_symbol: str = "",
    wallet: str = "",
    risk_score: int = 0,
    metadata: dict[str, Any] | None = None,
) -> str:
    """
    Push a new alert to the pipeline.
    Returns alert_id.
    """
    alert_id = f"alt_{int(time.time())}_{os.urandom(4).hex()}"

    alert = {
        "id": alert_id,
        "type": alert_type,
        "title": title,
        "description": description,
        "severity": severity,
        "chain": chain,
        "token": token,
        "token_symbol": token_symbol,
        "wallet": wallet,
        "risk_score": risk_score,
        "acknowledged": False,
        "created_at": datetime.now(UTC).isoformat(),
        **(metadata or {}),
    }

    try:
        r = await _get_redis()
        score = time.time()
        await r.zadd(ALERT_KEY, {json.dumps(alert): score})
        # Trim old alerts
        await r.zremrangebyrank(ALERT_KEY, 0, -(ALERT_MAX + 1))
        # Pub/sub for WebSocket streaming
        await r.publish("rmi:ws:alerts", json.dumps(alert))
        await r.close()
        logger.info(f"Alert pushed: {alert_type} | {title[:60]}")
    except Exception as e:
        logger.error(f"Failed to push alert: {e}")

    return alert_id


async def get_recent_alerts(limit: int = 20, severity: str = "") -> list[dict[str, Any]]:
    """Get recent alerts with optional severity filter. Normalizes old/new formats."""
    try:
        r = await _get_redis()
        raw = await r.zrevrange(ALERT_KEY, 0, limit * 2 - 1)
        await r.close()

        alerts = []
        for entry in raw:
            try:
                alert = json.loads(entry)

                # Normalize old alert format to new
                if "title" not in alert:
                    alert["title"] = alert.get("message", alert.get("event", "Unknown alert"))
                if "description" not in alert:
                    desc_parts = []
                    if alert.get("symbol"):
                        desc_parts.append(f"Token: {alert['symbol']}")
                    if alert.get("risk_score"):
                        desc_parts.append(f"Risk: {alert['risk_score']}/100")
                    flags = alert.get("risk_flags", [])
                    if flags:
                        desc_parts.append("; ".join(str(f)[:80] for f in flags[:2]))
                    alert["description"] = " | ".join(desc_parts)
                if "severity" not in alert:
                    score = alert.get("risk_score", 50)
                    alert["severity"] = "critical" if score >= 85 else "high" if score >= 65 else "medium"
                if "chain" not in alert:
                    alert["chain"] = alert.get("chain", "unknown")

                if severity and alert.get("severity") != severity:
                    continue
                alerts.append(alert)
                if len(alerts) >= limit:
                    break
            except json.JSONDecodeError:
                pass

        return alerts
    except Exception as e:
        logger.error(f"get_recent_alerts error: {e}")
        return []


# ── Alert generators - called by cron jobs or on-demand ──────────


async def scan_solana_new_pairs(limit: int = 5) -> int:
    """
    Scan latest Solana pairs from DexScreener for scam patterns.
    Pushes alerts for high-risk tokens.
    Returns number of alerts generated.
    """
    import httpx

    pushed = 0

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://api.dexscreener.com/latest/dex/search", params={"q": "SOL USDC"})
            if resp.status_code != 200:
                return 0

            data = resp.json()
            pairs = data.get("pairs", [])[:limit]

            for pair in pairs:
                token_addr = pair.get("baseToken", {}).get("address", "")
                token_name = pair.get("baseToken", {}).get("name", "Unknown")
                token_symbol = pair.get("baseToken", {}).get("symbol", "???")
                liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                volume = float(pair.get("volume", {}).get("h24", 0) or 0)
                price_change = float(pair.get("priceChange", {}).get("h24", 0) or 0)
                created_at = pair.get("pairCreatedAt", 0)
                age_hours = (time.time() - created_at / 1000) / 3600 if created_at else 999

                # Risk heuristics
                risk_flags = []

                if liquidity < 1000:
                    risk_flags.append("low_liquidity")
                if volume == 0:
                    risk_flags.append("no_volume")
                if price_change < -80:
                    risk_flags.append(f"crashed_{abs(price_change):.0f}%")
                if age_hours < 1 and liquidity < 5000:
                    risk_flags.append("fresh_launch_low_liq")
                if price_change > 500:
                    risk_flags.append(f"pumped_{price_change:.0f}%")

                if risk_flags:
                    await push_alert(
                        alert_type="new_pair_risk",
                        title=f"{token_symbol}: {' | '.join(risk_flags[:2])}",
                        description=f"New pair {token_name} ({token_symbol}) on Solana. "
                        f"Liquidity: ${liquidity:,.0f}, Age: {age_hours:.1f}h, "
                        f"24h change: {price_change:+.1f}%",
                        severity="critical" if len(risk_flags) >= 3 else "high",
                        chain="solana",
                        token=token_addr,
                        token_symbol=token_symbol,
                        risk_score=min(90, len(risk_flags) * 25),
                        metadata={
                            "risk_flags": risk_flags,
                            "liquidity_usd": liquidity,
                            "age_hours": age_hours,
                        },
                    )
                    pushed += 1

        return pushed
    except Exception as e:
        logger.warning(f"Solana scan error: {e}")
        return pushed


async def scan_known_scams(limit: int = 3) -> int:
    """
    Check our RAG known_scams collection for recently added entries.
    Pushes alerts for new scam patterns detected.
    """
    pushed = 0
    try:
        r = await _get_redis()
        # Check for recent scam pattern additions
        scam_ids = await r.smembers("rag:idx:known_scams")

        recent_count = 0
        for sid in list(scam_ids)[:20]:
            doc = await r.get(f"rag:known_scams:{sid}")
            if doc:
                try:
                    data = json.loads(doc)
                    added = data.get("metadata", {}).get("added_at", "")
                    if added:
                        age_h = (time.time() - datetime.fromisoformat(added.replace("Z", "+00:00")).timestamp()) / 3600
                        if age_h < 24:
                            recent_count += 1
                except json.JSONDecodeError:
                    pass

        await r.close()

        if recent_count > 0:
            await push_alert(
                alert_type="scam_pattern_update",
                title=f"{recent_count} new scam patterns detected in last 24h",
                description="New rug pull, honeypot, or phishing patterns added to the knowledge base.",
                severity="high",
                chain="multi",
                risk_score=85,
            )
            pushed += 1

        return pushed
    except Exception as e:
        logger.warning(f"Known scams scan error: {e}")
        return pushed


async def run_alert_scan() -> dict[str, int]:
    """
    Run a full alert scan across all sources.
    Called by cron job every 15 minutes.
    """
    results = {}

    # Scan Solana new pairs
    results["solana_pairs"] = await scan_solana_new_pairs(limit=8)

    # Scan known scams
    results["known_scams"] = await scan_known_scams()

    total = sum(results.values())
    logger.info(f"Alert scan complete: {total} new alerts ({results})")
    return results


# ── Seed some initial alerts if Redis is empty ────────────────────


async def seed_initial_alerts() -> None:
    """Seed baseline alerts so the system isn't empty on first run."""
    r = await _get_redis()
    existing = await r.zcard(ALERT_KEY)
    await r.close()

    if existing > 0:
        return  # Already has alerts

    seeds = [
        (
            "honeypot",
            "Honeypot detected on Base: 0xdead...",
            "Token has sell restrictions and blacklist. Buyers cannot exit.",
            "critical",
            "base",
        ),
        (
            "whale",
            "Whale moved 5M USDC to Binance",
            "Wallet 0xABCD... transferred $5M USDC to Binance hot wallet. Possible sell pressure.",
            "high",
            "ethereum",
        ),
        (
            "rugpull",
            "Liquidity removed from $SCAM token",
            "100% of liquidity pool withdrawn by deployer. Token is now worthless.",
            "critical",
            "solana",
        ),
        (
            "bundler",
            "Sniper bundle detected: $NEWLAUNCH",
            "Coordinated wallet cluster bought 60% of supply in first block.",
            "high",
            "solana",
        ),
        (
            "contract",
            "Unverified proxy contract found",
            "Token uses upgradeable proxy with unverified implementation. Owner can change logic.",
            "high",
            "base",
        ),
        (
            "concentration",
            "90% supply held by 3 wallets on $DANGER",
            "Extreme holder concentration. Classic rug pull setup.",
            "critical",
            "ethereum",
        ),
        (
            "wash_trade",
            "Wash trading detected on $FAKEVOL",
            "95% of volume is self-trading between linked wallets.",
            "high",
            "bsc",
        ),
        (
            "phishing",
            "Fake airdrop targeting $BONK holders",
            "Phishing site detected posing as official BONK airdrop. Users losing funds.",
            "critical",
            "solana",
        ),
    ]

    for alert_type, title, desc, severity, chain in seeds:
        await push_alert(
            alert_type=alert_type,
            title=title,
            description=desc,
            severity=severity,
            chain=chain,
        )

    logger.info(f"Seeded {len(seeds)} initial alerts")
