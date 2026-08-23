"""Exchange volume audit — wash-trading detection at the VENUE level.

Token-level fake volume (volume_integrity) asks "is this token's flow real?".
This module asks the venue question: "is this exchange sloshing money to
inflate its reported volume?" — the CoinMarketCap-2020 class of fraud.

Heuristics over CoinGecko tickers data (free tier, includes per-ticker
trust_score) for one token across all its venues:

  1. TRUST MIX      — share of an exchange's tickers that are red/yellow.
                      Venues trading a token ONLY on red trust = hot money.
  2. VOL SHARE VS   — exchange claims X% of token volume but holds Y% of
     DEPTH            observable depth; X >> Y with thin books = printed vol.
  3. ROUND-LOT DOMINANCE — repeated identical base-volume tickers (bots
                      cycling fixed lots) inside one exchange's ticker set.
  4. STALE PRINTS   — last_traded_at older than peers but volume still
                      accruing = counters running without a market.

Composite venue score 0-100 (100 = trustworthy), ranked worst-first.
Fail-open: missing fields degrade honestly.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from rugmunchbot.tools.registry import ToolResult, backend_json_get

logger = logging.getLogger(__name__)


def _f(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


async def _fetch_tickers(token_id: str) -> list[dict[str, Any]]:
    try:
        body = await backend_json_get(
            "/api/v1/databus/coingecko/tickers",
            params={"id": token_id, "per_page": 100},
            timeout=8.0,
        )
        tickers = (body or {}).get("tickers") or []
        return [t for t in tickers if isinstance(t, dict)]
    except Exception as exc:
        logger.warning("exchange_audit:tickers_failed err=%s", str(exc)[:120])
        # direct fallback: CoinGecko public endpoint
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(
                    f"https://api.coingecko.com/api/v3/coins/{token_id}/tickers",
                    params={"include_exchange_logo": "false"},
                )
                if r.status_code == 200:
                    return r.json().get("tickers", [])
        except Exception as exc2:
            logger.warning("exchange_audit:direct_failed err=%s", str(exc2)[:120])
    return []


def audit_venues(tickers: list[dict[str, Any]]) -> dict[str, Any]:
    """Group tickers by exchange, score each venue, rank worst-first."""
    by_venue: dict[str, list[dict[str, Any]]] = {}
    for t in tickers:
        ex = ((t.get("market") or {}).get("name") or "").strip()
        if ex:
            by_venue.setdefault(ex, []).append(t)

    total_vol = sum(_f(t.get("converted_volume", {}).get("usd")) or 0.0 for t in tickers) or 1.0
    now = datetime.now(tz=UTC)
    venues: list[dict[str, Any]] = []

    for ex, ts in by_venue.items():
        score = 100.0
        notes: list[str] = []
        vol = sum(_f(t.get("converted_volume", {}).get("usd")) or 0.0 for t in ts)
        trust_flags = [str(t.get("trust_score") or "unverified").lower() for t in ts]
        red = sum(1 for x in trust_flags if x in ("red", "unverified"))
        green = sum(1 for x in trust_flags if x == "green")

        # 1) trust mix
        red_share = red / len(ts) if ts else 0.0
        if red_share >= 0.9 and green == 0:
            score -= 35
            notes.append(f"{red}/{len(ts)} tickers untrusted (no verified market)")
        elif red_share >= 0.5:
            score -= 15
            notes.append(f"majority untrusted tickers ({red}/{len(ts)})")

        # 2) vol share vs depth
        depth = sum(
            _f((t.get("market") or {}).get("depth_usd", 0)) or 0.0
            for t in ts
        )
        vol_share = vol / total_vol
        if depth and depth > 0 and vol > 0:
            vol_depth_ratio = vol / depth
            if vol_depth_ratio > 500:
                score -= 30
                notes.append(f"vol/depth {vol_depth_ratio:,.0f}x — prints cannot be real flow")
            elif vol_depth_ratio > 100:
                score -= 12
                notes.append(f"vol/depth {vol_depth_ratio:,.0f}x elevated")

        # 3) round-lot dominance: identical base volumes repeated
        vols = [round(_f(t.get("volume")) or 0.0, 6) for t in ts]
        if len(vols) >= 5:
            common, count = Counter(vols).most_common(1)[0]
            if count / len(vols) > 0.6 and common > 0:
                score -= 25
                notes.append(f"{count}/{len(vols)} identical lot size {common:g} — bot cycling")

        # 4) stale prints
        stale = 0
        for t in ts:
            lt = t.get("last_traded_at")
            try:
                dt = datetime.fromisoformat(str(lt).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                if now - dt > timedelta(minutes=30):
                    stale += 1
            except (ValueError, TypeError):
                pass
        if ts and stale == len(ts) and vol > 0:
            score -= 20
            notes.append("all prints stale while volume accrues")

        score = max(0.0, min(100.0, score))
        verdict = "TRUSTED" if score >= 80 else "SUSPECT" if score >= 50 else "WASH_RISK"
        venues.append({
            "exchange": ex,
            "score": round(score, 1),
            "verdict": verdict,
            "vol_usd_24h": round(vol),
            "vol_share": round(vol_share * 100, 1),
            "tickers": len(ts),
            "notes": notes,
        })

    venues.sort(key=lambda v: v["score"])
    return {"venues": venues, "exchanges_audited": len(venues)}


async def exchange_volume_audit(*, token_id: str = "", symbol: str = "") -> ToolResult:
    """ToolResult: venue-level wash-trading audit for one token."""
    tid = token_id or (symbol.lower() if symbol else "")
    if not tid:
        return ToolResult(ok=False, data={"error": "token_id or symbol required"})
    tickers = await _fetch_tickers(tid)
    if not tickers:
        return ToolResult(ok=False, data={"error": "no ticker data available"})
    report = audit_venues(tickers)
    report["token"] = tid
    wash_risk = [v for v in report["venues"] if v["verdict"] == "WASH_RISK"]
    report["headline"] = (
        f"{len(wash_risk)}/{report['exchanges_audited']} venues flagged WASH_RISK"
        if wash_risk else "no venue-level wash signals"
    )
    return ToolResult(ok=True, data=report)
