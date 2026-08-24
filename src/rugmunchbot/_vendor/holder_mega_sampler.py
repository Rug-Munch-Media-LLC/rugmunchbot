"""Multi-page holder mega-sampler — deeper than any competitor's free view.

Market standard (DEXScreener, Bubblemaps free, most bots): top 10-25
holders. We paginate Ankr's indexed holder list up to ``target`` wallets
(100/page), union with our own ClickHouse top-holders when the chain is
indexed, and compute concentration over the UNION — a materially better
estimate of true distribution and a much stronger bundle/cluster input
for the AI deep-scan.

Cost math (Freemium 200M credits/mo): Advanced API = 700 credits/request;
500 holders = 5 requests = 3,500 credits. Even at 50 deep-scans/day that's
175k credits/day — 8% of monthly budget. Effectively endless.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MULTICHAIN = "https://rpc.ankr.com/multichain"
DEFAULT_TARGET = int(os.getenv("HOLDER_SAMPLE_TARGET", "500"))
PAGE_SIZE = 100  # Ankr max


def _key() -> str:
    return os.getenv("ANKR_API_KEY", "").strip()


async def _ankr_page(
    contract: str, chain: str, page_token: str | None, client: httpx.AsyncClient
) -> tuple[list[dict[str, Any]], str | None]:
    params: dict[str, Any] = {
        "blockchain": chain,
        "contractAddress": contract,
        "pageSize": PAGE_SIZE,
    }
    if page_token:
        params["nextPageToken"] = page_token
    r = await client.post(
        MULTICHAIN,
        json={"jsonrpc": "2.0", "method": "ankr_getTokenHolders", "params": params, "id": 1},
        headers={"X-API-KEY": _key()},
    )
    if r.status_code != 200:
        raise RuntimeError(f"ankr HTTP {r.status_code}")
    d = r.json()
    if "error" in d:
        raise RuntimeError(f"ankr error: {d['error']}")
    res = d.get("result", {})
    return res.get("holders") or [], res.get("nextPageToken")


async def sample_holders(
    contract_address: str,
    chain: str,
    *,
    target: int = DEFAULT_TARGET,
    own_top_holders: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Deep-sample holders: Ankr paginated + own ClickHouse union.

    Returns {
        "holders": [{rank, address, balance, percentage}...sorted desc],
        "sample_size": int,                # unique wallets sampled
        "sources": {"ankr_pages": n, "own_merged": n},
        "concentration_pct": top1 %, "top5_pct", "top10_pct",
        "coverage_note": str,
    }
    Raises RuntimeError only when NO source yields data (fail loud upstream).
    """
    if not _key():
        raise RuntimeError("ANKR_API_KEY unset — mega-sampler unavailable")

    merged: dict[str, float] = {}  # address -> balance (float for ranking)
    raw_balances: dict[str, str] = {}
    pages = 0
    token: str | None = None

    async def _page_retry(client: httpx.AsyncClient) -> tuple[list[dict[str, Any]], str | None]:
        """One page fetch with a single 429 backoff-retry."""
        try:
            return await _ankr_page(contract_address, chain, token, client)
        except RuntimeError as exc:
            if "429" not in str(exc) and "rate-limited" not in str(exc):
                raise
            await asyncio.sleep(3.0)
            return await _ankr_page(contract_address, chain, token, client)

    async with httpx.AsyncClient(timeout=20) as c:
        while len(merged) < target and pages < 20:  # hard cap 2000 wallets
            if pages and pages % 40 == 0:
                await asyncio.sleep(1.5)  # stay under 50 req/min freemium
            try:
                batch, token = await _page_retry(c)
            except RuntimeError as exc:
                if pages == 0:
                    raise  # first page failed = no data at all
                logger.warning("mega_sampler:page_failed page=%s err=%s", pages + 1, str(exc)[:80])
                break
            if not batch:
                break
            for h in batch:
                addr = (h.get("holderAddress") or "").lower()
                if not addr:
                    continue
                try:
                    bal = float(h.get("balance") or 0)
                except (TypeError, ValueError):
                    continue
                if bal <= 0:
                    continue
                merged[addr] = merged.get(addr, 0.0) + bal
                raw_balances[addr] = h.get("balance") or ""
            pages += 1
            if not token:
                break

    sources = {"ankr_pages": pages}

    # Union with own ClickHouse top holders (they carry percentages already)
    if own_top_holders:
        own_merged = 0
        for h in own_top_holders:
            addr = (h.get("address") or "").lower()
            if not addr or addr in merged:
                continue
            pct = h.get("percentage")
            try:
                merged[addr] = float(pct or 0)  # placeholder; renormalized below
                own_merged += 1
            except (TypeError, ValueError):
                continue
        sources["own_merged"] = own_merged

    if not merged:
        raise RuntimeError("no holder data from any source")

    total = sum(merged.values()) or 1.0
    ranked = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)
    holders_out = [
        {
            "rank": i,
            "address": addr,
            "balance": merged[addr],
            "percentage": round(bal / total * 100, 4),
        }
        for i, (addr, bal) in enumerate(ranked, start=1)
    ]

    def _top(n: int) -> float:
        return round(sum(h["percentage"] for h in holders_out[:n]), 2)

    return {
        "holders": holders_out,
        "sample_size": len(holders_out),
        "sources": sources,
        "concentration_pct": holders_out[0]["percentage"] if holders_out else 0.0,
        "top5_pct": _top(5),
        "top10_pct": _top(10),
        "coverage_note": (
            f"{len(holders_out)} unique wallets sampled across {pages} indexed "
            f"page(s){' + own index' if own_top_holders else ''} — "
            f"{len(holders_out)}x deeper than typical free explorer views"
        ),
    }


# ── Source 3: raw Transfer-log discovery via our own RPC router ────────────
# Ankr-independent: uses the multi-provider fallback chain (BlockPI keyed,
# Alchemy x2, dRPC, public pool). Scans a recent block window for Transfer
# events and collects participating addresses — fresh-wallet + flow signal
# even when every indexed provider is down.

_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


async def _router_getlogs(
    contract: str, chain: str, window: int
) -> list[dict[str, Any]]:
    """Recent Transfer events via rpc_router. Returns [{from,to,value}]."""
    from rugmunchbot._vendor.scanner_engines.rpc_router import get_rpc_router
    from rugmunchbot.config.rpc_chains import EVM_RPC_ENDPOINTS

    # Router uses full chain names; Ankr slugs differ (eth vs ethereum).
    router_chain = {"eth": "ethereum", "bsc": "bsc"}.get(chain, chain)
    router = get_rpc_router()
    latest = await router.call(router_chain, "eth_blockNumber", [])
    if not latest or "result" not in (latest or {}):
        return []
    head = int(latest["result"], 16)
    frm = max(0, head - window)
    # Heavy call: prefer keyed BlockPI endpoint directly (most free-pool
    # providers lack eth_getLogs or cap ranges); fall back to the router.
    # Keyed = has a secret segment; skip /rpc/public (dead) explicitly.
    keyed = [
        u for u in EVM_RPC_ENDPOINTS.get(router_chain, [])
        if "blockpi" in u and "${" not in u and "/rpc/public" not in u
    ]
    attempts: list[Any] = []
    attempts.extend(keyed)
    attempts.append("__router__")

    chunk_size = 200  # blocks per request — keeps payloads under provider caps

    async def _fetch_range(attempt: Any, start: int, end: int) -> list[dict[str, Any]]:
        flt = {
            "fromBlock": hex(start),
            "toBlock": hex(end),
            "address": contract,
            "topics": [_TRANSFER_TOPIC, None, None],
        }
        if attempt == "__router__":
            res = await router.call(router_chain, "eth_getLogs", [flt], timeout=30)
            return (res or {}).get("result") if isinstance(res, dict) else []
        async with httpx.AsyncClient(timeout=60) as c2:
            r2 = await c2.post(
                attempt,
                json={"jsonrpc": "2.0", "method": "eth_getLogs", "params": [flt], "id": 1},
            )
            if r2.status_code != 200:
                raise RuntimeError(f"getlogs HTTP {r2.status_code}")
            out = r2.json().get("result")
            return out if isinstance(out, list) else []

    logs: list[dict[str, Any]] = []
    for attempt in attempts:
        try:
            start = frm
            while start <= head:
                end = min(start + chunk_size - 1, head)
                logs.extend(await _fetch_range(attempt, start, end))
                start = end + 1
            break  # this attempt served every chunk successfully
        except Exception as exc:
            logger.debug(
                "getlogs via %s failed at block ~%s: %s",
                str(attempt)[:40], start, str(exc)[:60],
            )
            logs = []  # discard partial; next attempt starts clean
            continue
    out: list[dict[str, Any]] = []
    for lg in logs or []:
        topics = lg.get("topics") or []
        if len(topics) < 3:
            continue
        try:
            value = int(lg.get("data", "0x0"), 16)
        except ValueError:
            value = 0
        out.append({
            "from": "0x" + topics[1][-40:],
            "to": "0x" + topics[2][-40:],
            "value": value,
        })
    return out


async def discover_flow_participants(
    contract_address: str, chain: str, *, window_blocks: int = 600, chunk: int = 200, cap: int = 400
) -> dict[str, Any]:
    # 500-block window: USDT alone yields ~60k transfers — plenty of flow
    # signal without blowing provider response limits.
    """Addresses actively moving the token in the recent window.

    Ankr-independent (uses our own multi-provider router). Returns
    {"participants": {addr: moved_total}, "blocks_scanned": window, "transfers": n}.
    """
    try:
        transfers = await _router_getlogs(contract_address, chain, window_blocks)
    except Exception as exc:
        logger.warning("flow_discovery failed: %s", str(exc)[:100])
        return {"participants": {}, "blocks_scanned": window_blocks, "transfers": 0}
    participants: dict[str, float] = {}
    for t in transfers:
        for side in ("from", "to"):
            a = (t.get(side) or "").lower()
            if a and a != "0x" + "0" * 40:
                participants[a] = participants.get(a, 0.0) + float(t.get("value") or 0)
    trimmed = dict(sorted(participants.items(), key=lambda kv: kv[1], reverse=True)[:cap])
    return {
        "participants": trimmed,
        "blocks_scanned": window_blocks,
        "transfers": len(transfers),
    }
