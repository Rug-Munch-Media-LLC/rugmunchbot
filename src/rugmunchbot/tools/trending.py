"""trending tool — REAL currently-trending tokens.

Sources (in order):
  1. Our backend ``/api/v1/market/trending`` — fused RugCharts + DexScreener
     + CoinGecko trending (own-data first, backend-cached).
  2. Backend ``/api/v1/news/trending`` — trending news headlines.
  3. DexScreener token-boosts (``/token-boosts/top/v1``) — direct, free.

All token symbols/addresses in the result come from those live sources.
The tool GUARANTEES every emitted token carries a real on-chain address:
CoinGecko-sourced feed entries carry no contract address (the backend leaves
``address`` empty / ``chain == "multi"``), so they are resolved through
DexScreener search (``/latest/dex/search?q=<symbol or name>``) and DROPPED
when resolution fails — the answer can never degrade into an
``[address not verified]`` placeholder.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.domains.telegram.rugmunchbot.config import DEXSCREENER, DEXSCREENER_TOKEN_BOOSTS
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_json
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult, backend_json_get

logger = get_logger("rmi_bot.tools.trending")

# Address-resolution pass: bounded concurrency + hard deadline so the extra
# DexScreener searches never blow the 6s registry timeout for this tool
# (partial results still return — tokens that didn't resolve are dropped).
_RESOLVE_CONCURRENCY = 5
_RESOLVE_DEADLINE = 4.0  # seconds

# Chain-name aliases -> canonical DexScreener-style chain id. Mirrors
# tools/intent.py + tools/trend_algo.py (keep in sync). Used to filter the
# cross-chain feed down to the chain the user actually asked about.
_CHAIN_ALIASES: dict[str, str] = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "solana": "solana",
    "sol": "solana",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "binance-smart-chain": "bsc",
    "binance": "bsc",
    "bsc": "bsc",
    "bnb": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "optimism": "optimism",
    "op": "optimism",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "berachain": "berachain",
    "bera": "berachain",
    "hyperliquid": "hyperliquid",
    "zksync": "zksync",
    "robinhood": "robinhood",
    "injective": "injective",
    "fantom": "fantom",
    "ftm": "fantom",
    "linea": "linea",
    "blast": "blast",
    "sonic": "sonic",
    "scroll": "scroll",
    "mantle": "mantle",
    "tron": "tron",
    "trx": "tron",
    "ton": "ton",
    "base": "base",
    "sei": "sei",
    "sui": "sui",
    "aptos": "aptos",
    "mode": "mode",
}


def _normalize_chain(chain: str) -> str:
    """Return canonical chain id ('' / 'all' / 'multi' stay as-is)."""
    key = (chain or "").strip().lower()
    if key in ("", "all", "any", "multi"):
        return key
    return _CHAIN_ALIASES.get(key, key)


def _filter_tokens_by_chain(tokens: list[dict[str, Any]], chain: str) -> list[dict[str, Any]]:
    """Keep only tokens on the requested chain (pure, deterministic).

    ``chain`` '' / 'all' / 'multi' -> passthrough (cross-chain feed).
    Otherwise a token is kept when its normalized ``chain`` field equals the
    requested chain. CoinGecko entries carry ``chain == "multi"`` and no
    on-chain address, so they are dropped for a chain-scoped question (they
    are not scanable and not on the requested chain).
    """
    want = _normalize_chain(chain)
    if want in ("", "all", "multi"):
        return list(tokens)
    return [t for t in tokens if _normalize_chain(str(t.get("chain") or "")) == want]


async def _resolve_missing_address(symbol: str, name: str, want_chain: str = "") -> tuple[str, str] | None:
    """Resolve a real on-chain address for a trending token that lacks one.

    Queries DexScreener ``/latest/dex/search?q=<symbol or name>`` and picks
    the best pair: exact ``baseToken.symbol`` match preferred over exact
    ``baseToken.name`` match, higher 24h volume wins ties. When
    ``want_chain`` is a concrete chain (not ``''`` / ``'all'`` / ``'multi'``)
    only pairs on that chain are considered. Returns ``(address, chain_id)``
    or ``None`` when nothing matches — the caller drops the token (a clean
    list beats a hedge). Never raises (``http_get_json`` is fail-open).
    """
    q = (symbol or "").strip() or (name or "").strip()
    if not q:
        return None
    body = await http_get_json(f"{DEXSCREENER}/search", params={"q": q}, timeout=6.0)
    pairs = body.get("pairs") if isinstance(body, dict) else None
    if not isinstance(pairs, list) or not pairs:
        return None
    want = _normalize_chain(want_chain)
    sym_upper = (symbol or "").strip().upper()
    name_lower = (name or "").strip().lower()
    best_sym: tuple[float, str, str] | None = None  # (volume, address, chain)
    best_name: tuple[float, str, str] | None = None
    for p in pairs:
        if not isinstance(p, dict):
            continue
        base = p.get("baseToken")
        if not isinstance(base, dict):
            continue
        addr = str(base.get("address") or "").strip()
        if not addr:
            continue
        chain = _normalize_chain(str(p.get("chainId") or ""))
        if want not in ("", "all", "multi") and chain != want:
            continue
        if not (sym_upper or name_lower):
            continue
        try:
            vol = float(((p.get("volume") or {}).get("h24")) or 0) if isinstance(p.get("volume"), dict) else 0.0
        except (TypeError, ValueError):
            vol = 0.0
        if sym_upper and str(base.get("symbol") or "").strip().upper() == sym_upper:
            if best_sym is None or vol > best_sym[0]:
                best_sym = (vol, addr, chain)
        elif (
            name_lower
            and str(base.get("name") or "").strip().lower() == name_lower
            and (best_name is None or vol > best_name[0])
        ):
            best_name = (vol, addr, chain)
    if best_sym is not None:
        return best_sym[1], best_sym[2]
    if best_name is not None:
        return best_name[1], best_name[2]
    return None


async def _ensure_addresses(tokens: list[dict[str, Any]], chain: str) -> list[dict[str, Any]]:
    """Guarantee every emitted trending token carries a real address.

    Tokens missing an address (CoinGecko-sourced entries: name+symbol only)
    are resolved through DexScreener search matched by symbol/chain; tokens
    that cannot be resolved are DROPPED. Duplicate addresses (the same token
    surfacing from both the DEX and CoinGecko legs of the feed) collapse to
    the first occurrence. Bounded by ``_RESOLVE_CONCURRENCY`` and a hard
    deadline so the tool stays inside its registry timeout.
    """
    missing = [(i, t) for i, t in enumerate(tokens) if not (t.get("address") or "").strip()]
    if not missing:
        return tokens

    sem = asyncio.Semaphore(_RESOLVE_CONCURRENCY)

    async def resolve_one(idx: int, tok: dict[str, Any]) -> tuple[int, tuple[str, str] | None]:
        async with sem:
            want = chain or str(tok.get("chain") or "")
            return idx, await _resolve_missing_address(str(tok.get("symbol") or ""), str(tok.get("name") or ""), want)

    tasks = [asyncio.ensure_future(resolve_one(i, t)) for i, t in missing]
    done, pending = await asyncio.wait(tasks, timeout=_RESOLVE_DEADLINE)
    for t in pending:
        t.cancel()
    if pending:
        logger.warning(
            "trending address resolution hit deadline — %d/%d unresolved tokens dropped",
            len(pending),
            len(missing),
        )
    resolved: dict[int, tuple[str, str]] = {}
    for task in done:
        try:
            idx, result = task.result()
            if result is not None:
                resolved[idx] = result
        except Exception as exc:  # pragma: no cover — per-call failures already swallowed
            logger.debug("trending address resolution failed: %s", exc)

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, tok in enumerate(tokens):
        addr = (tok.get("address") or "").strip()
        chain_id = str(tok.get("chain") or "")
        if not addr:
            hit = resolved.get(i)
            if hit is None:
                logger.info(
                    "trending drop unresolved token symbol=%s name=%.40s",
                    str(tok.get("symbol") or "-"),
                    str(tok.get("name") or ""),
                )
                continue
            addr, chain_id = hit
            tok["address"] = addr
            # CoinGecko entries carry chain "multi" (or empty) — fill the REAL
            # chain from the pair so the answer shows the actual chain tag.
            if _normalize_chain(str(tok.get("chain") or "")) in ("", "multi"):
                tok["chain"] = chain_id
        norm = f"{addr.strip().lower()}|{_normalize_chain(chain_id)}"
        if norm in seen:
            continue  # same token from both feed legs — first occurrence wins
        seen.add(norm)
        out.append(tok)
    return out


async def _fetch_market_trending(chain: str = "all", limit: int = 10) -> tuple[list[dict[str, Any]], str] | None:
    body = await backend_json_get("/api/v1/market/trending", params={"limit": limit}, timeout=6.0)
    if not body:
        return None
    items = body.get("items")
    if not isinstance(items, list) or not items:
        return None
    tokens: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        tokens.append(
            {
                "symbol": it.get("symbol") or "",
                "name": it.get("name") or "",
                "address": it.get("address") or it.get("token_address") or "",
                "chain": it.get("chain") or "",
                "price_usd": it.get("price"),
                "change_24h": it.get("change_24h"),
                "volume_24h": it.get("volume_24h"),
                "market_cap": it.get("market_cap"),
                "rank": it.get("rank"),
                "source": it.get("source") or "backend",
            }
        )
    tokens = _filter_tokens_by_chain(tokens, chain)
    if not tokens:
        return None
    return tokens, "backend:/api/v1/market/trending"


async def _fetch_token_boosts(chain: str = "all", limit: int = 10) -> tuple[list[dict[str, Any]], str] | None:
    body = await http_get_json(DEXSCREENER_TOKEN_BOOSTS, timeout=6.0)
    if not isinstance(body, list) or not body:
        return None
    tokens: list[dict[str, Any]] = []
    for it in body[:limit]:
        if not isinstance(it, dict):
            continue
        tokens.append(
            {
                "symbol": (it.get("description") or "").split(".")[0].replace("$", "").strip(),
                "name": (it.get("description") or "").split(".")[0].replace("$", "").strip(),
                "address": it.get("tokenAddress") or "",
                "chain": it.get("chainId") or "",
                "url": it.get("url") or "",
                "source": "dexscreener:token-boosts",
            }
        )
    tokens = _filter_tokens_by_chain(tokens, chain)
    if not tokens:
        return None
    return tokens, "dexscreener:token-boosts"


async def trending(*, chain: str = "all", limit: int = 10) -> ToolResult:
    """Fetch real trending tokens, chain-filtered (backend fused source first).

    Args:
        chain: canonical chain id ("solana", "ethereum", "base", ...) or
            "all" for the raw cross-chain feed. The user's requested chain
            must arrive here via the intent router (tool_kwargs) — this tool
            FILTERS the live sources, it never fabricates a chain.
        limit: max tokens to return.
    """
    tokens, source = (
        (await _fetch_market_trending(chain, limit)) or (await _fetch_token_boosts(chain, limit)) or (None, "")
    )
    if tokens is None:
        return ToolResult(tool="trending", ok=False, error="no live trending data (backend + DexScreener unavailable)")

    # Every emitted token must carry a real on-chain address — resolve the
    # addressless CoinGecko entries or drop them (never a placeholder).
    tokens = await _ensure_addresses(tokens, chain)
    if not tokens:
        return ToolResult(
            tool="trending",
            ok=False,
            error="no trending tokens with a verifiable on-chain address",
        )

    return ToolResult(
        tool="trending",
        ok=True,
        data={"chain": _normalize_chain(chain), "limit": limit, "tokens": tokens},
        symbols=[t["symbol"] for t in tokens if t.get("symbol")],
        addresses=[t["address"] for t in tokens if t.get("address")],
        source=source,
    )


async def news_headlines(*, limit: int = 5) -> ToolResult:
    """Real top news headlines from our backend news feed."""
    body = await backend_json_get("/api/v1/news/trending", params={"limit": limit}, timeout=6.0)
    if not body:
        return ToolResult(
            tool="news_headlines", ok=False, error="no live news (backend /api/v1/news/trending unavailable)"
        )
    items = body.get("items") or body.get("articles") or []
    headlines = []
    for it in items[:limit]:
        if not isinstance(it, dict):
            continue
        title = (it.get("title") or "").strip()
        if title:
            headlines.append({"title": title, "url": it.get("url") or "", "source": it.get("source") or ""})
    if not headlines:
        return ToolResult(tool="news_headlines", ok=False, error="no headlines returned")
    return ToolResult(
        tool="news_headlines",
        ok=True,
        data={"headlines": headlines},
        symbols=[],
        addresses=[],
        source="backend:/api/v1/news/trending",
    )
