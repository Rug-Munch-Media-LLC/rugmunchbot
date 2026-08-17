"""token_meta tool — ALWAYS attach token identity + logo to scan/token results.

Every token/scan answer the bot produces must show the token image/logo plus
name, symbol, chain and price. This module resolves that identity block in a
strict fallback order — our own data is preferred, external providers are used
only when our backend is down or rate-limited:

  1. Own backend DataBus token detail (``GET /api/v1/databus/token/{address}``)
     — our DataBus aggregate (DexScreener + Birdeye + GMGN + our scanners),
     served from OUR backend with the shared-secret header. The pair's
     ``info.imageUrl`` is the token logo.
  2. Own backend scanner result cache (``GET /api/v2/scanner/token/full/{chain}/{address}``)
     — the last SENTINEL unified-scan result (name/symbol/price plus the
     SENTINEL metadata-fingerprint ``logo_url``, our own engine's image).
  3. DexScreener direct (``GET /latest/dex/tokens/{address}``) — the ``icon``
     is the pair's ``info.imageUrl``.
  4. CoinGecko (``GET /api/v3/search?query={symbol}``) — ``coins[0].large``.

The ``image_url`` field is ALWAYS present in the resolved block. When no
source produced a logo, it is the literal string ``"no image available"`` —
never silently omitted.
"""

from __future__ import annotations

import json
from typing import Any

from app.domains.telegram.rugmunchbot.config import COINGECKO, DEXSCREENER
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_json
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult, backend_json_get

logger = get_logger("rmi_bot.tools.token_meta")

# Canonical "no logo" value — callers render this verbatim instead of omitting
# the image field (the LLM then says "no image available" for this token).
NO_IMAGE_AVAILABLE = "no image available"

# Redis token-identity cache. Identity (name/symbol/chain/image) is stable for
# minutes — TTL 600s; the price component moves fast — TTL 60s, stored under
# its own key so it expires (and refreshes) independently of the identity.
_META_IDENTITY_TTL = 600
_META_PRICE_TTL = 60
_META_IDENTITY_PREFIX = "rmi:bot:token_meta:"
_META_PRICE_PREFIX = "rmi:bot:token_price:"

# Keys (in priority order) that our own scanner/backends may use for the token
# logo. Mirrors ``formatters._sentinel._token_image_url`` so both layers agree.
_IMAGE_KEYS = ("image_uri", "image_url", "logo_url", "image")


def _redis_client() -> Any:
    """Async Redis client (lazy import — fail-open when unavailable)."""
    from app.core.redis import get_redis_async

    return get_redis_async()


def _identity_key(address: str, chain: str) -> str:
    return f"{_META_IDENTITY_PREFIX}{chain}:{address}"


def _price_key(address: str, chain: str) -> str:
    return f"{_META_PRICE_PREFIX}{chain}:{address}"


def _json_loads(raw: Any) -> Any:
    """Decode a Redis bytes/str payload; None on malformed JSON."""
    try:
        return json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
    except (ValueError, TypeError, AttributeError):
        return None


async def _cache_get_meta(address: str, chain: str) -> tuple[dict[str, Any] | None, bool]:
    """Load the cached identity block for ``address``.

    Returns ``(meta, price_fresh)``:
      * ``(None, False)`` — no usable identity entry (cache miss);
      * ``(meta, True)`` — identity hit AND the 60s price sub-key is still
        fresh (price already merged into ``meta``);
      * ``(meta, False)`` — identity hit but the price sub-key expired/missing
        — the caller MUST re-resolve live so the price stays fresh.
    Fail-open: Redis down / auth failure → ``(None, False)`` (live resolution).
    """
    r = _redis_client()
    if r is None:
        return None, False
    try:
        raw = await r.get(_identity_key(address, chain))
        if not raw:
            return None, False
        data = _json_loads(raw)
        if not isinstance(data, dict):
            return None, False
        price_raw = await r.get(_price_key(address, chain))
        price_data = _json_loads(price_raw) if price_raw else None
        if isinstance(price_data, dict) and "price_usd" in price_data:
            data["price_usd"] = price_data["price_usd"]
            return data, True
        return data, False
    except Exception:  # fail-open by contract
        logger.debug("token_meta:cache_get_failed address=%.20s", address, exc_info=True)
        return None, False


async def _cache_set_meta(address: str, chain: str, meta: dict[str, Any]) -> None:
    """Write the identity block (600s) + price (60s) to Redis. Fail-open."""
    r = _redis_client()
    if r is None:
        return
    try:
        identity = {k: v for k, v in meta.items() if k != "price_usd"}
        await r.set(_identity_key(address, chain), json.dumps(identity, default=str), ex=_META_IDENTITY_TTL)
        price = meta.get("price_usd")
        if price is not None:
            await r.set(_price_key(address, chain), json.dumps({"price_usd": price}, default=str), ex=_META_PRICE_TTL)
    except Exception:  # fail-open by contract
        logger.debug("token_meta:cache_set_failed address=%.20s", address, exc_info=True)


def _detect_chain(address: str) -> str:
    """Mirror the backend's chain auto-detect: 0x → ethereum, else solana."""
    addr = (address or "").strip()
    if addr.startswith(("0x", "0X")):
        return "ethereum"
    return "solana"


def _first_http_value(payload: dict[str, Any]) -> str:
    """Return the first http(s) logo value found at any of the known keys."""
    for key in _IMAGE_KEYS:
        val = payload.get(key)
        if isinstance(val, str) and val.strip().startswith(("http://", "https://")):
            return val.strip()
    return ""


def _nested_image(payload: dict[str, Any]) -> str:
    """Search nested own-data structures for a logo URL.

    Order:
      1. ``sentinel.free.metadata_fingerprint.logo_url`` — our SENTINEL
         metadata-fingerprint engine's own resolved logo (DexScreener
         ``info.imageUrl`` fetched by the scanner itself).
      2. ``data.sentinel.free.metadata_fingerprint.logo_url`` — same field
         under the ``{cached, data}`` wrapper the scanner-data cache uses.
      3. ``data.info.imageUrl`` — a single pair under the databus wrapper.
    """
    sentinel = payload.get("sentinel")
    if isinstance(sentinel, dict):
        free = sentinel.get("free")
        if isinstance(free, dict):
            mf = free.get("metadata_fingerprint")
            if isinstance(mf, dict) and isinstance(mf.get("logo_url"), str):
                url = mf["logo_url"].strip()
                if url.startswith(("http://", "https://")):
                    return url
    data = payload.get("data")
    if isinstance(data, dict):
        sentinel = data.get("sentinel")
        if isinstance(sentinel, dict):
            free = sentinel.get("free")
            if isinstance(free, dict):
                mf = free.get("metadata_fingerprint")
                if isinstance(mf, dict) and isinstance(mf.get("logo_url"), str):
                    url = mf["logo_url"].strip()
                    if url.startswith(("http://", "https://")):
                        return url
        info = data.get("info")
        if isinstance(info, dict) and isinstance(info.get("imageUrl"), str):
            url = info["imageUrl"].strip()
            if url.startswith(("http://", "https://")):
                return url
    return ""


def _pair_image(pair: dict[str, Any]) -> str:
    """Extract the logo from a DexScreener-style pair (``info.imageUrl``)."""
    info = pair.get("info")
    if isinstance(info, dict) and isinstance(info.get("imageUrl"), str):
        url = info["imageUrl"].strip()
        if url.startswith(("http://", "https://")):
            return url
    return ""


def _best_pair(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the pair with the highest USD liquidity (identity source)."""
    best: dict[str, Any] | None = None
    best_liq = -1.0
    for p in pairs:
        liq = 0.0
        try:
            liq = float((p.get("liquidity") or {}).get("usd", 0) or 0)
        except (TypeError, ValueError):
            liq = 0.0
        if liq > best_liq:
            best_liq = liq
            best = p
    return best


def _first_pair_image(pairs: list[dict[str, Any]]) -> str:
    """First logo found across ALL pairs — image may live on a less-liquid pair."""
    for p in pairs:
        url = _pair_image(p)
        if url:
            return url
    return ""


def _pair_meta(pair: dict[str, Any]) -> dict[str, Any]:
    """Normalize one pair into the canonical token-identity block."""
    base = pair.get("baseToken") or {}
    addr = str(base.get("address") or "").strip()
    return {
        "address": addr,
        "name": str(base.get("name") or ""),
        "symbol": str(base.get("symbol") or ""),
        "chain": str(pair.get("chainId") or ""),
        "price_usd": pair.get("priceUsd"),
        "image_url": _pair_image(pair) or NO_IMAGE_AVAILABLE,
        "image_available": bool(_pair_image(pair)),
        "image_source": "dexscreener" if _pair_image(pair) else "none",
    }


def _finalize_meta(
    *,
    address: str,
    chain: str,
    name: str = "",
    symbol: str = "",
    price_usd: object = None,
    image_url: str = "",
    image_source: str = "none",
) -> dict[str, Any]:
    """Build the canonical token block. ``image_url`` is never omitted."""
    url = image_url.strip() if isinstance(image_url, str) else ""
    if not url.startswith(("http://", "https://")):
        url = NO_IMAGE_AVAILABLE
        image_source = "none"
    return {
        "address": address,
        "name": name or "",
        "symbol": symbol or "",
        "chain": chain or "",
        "price_usd": price_usd,
        "image_url": url,
        "image_available": url != NO_IMAGE_AVAILABLE,
        "image_source": image_source if url != NO_IMAGE_AVAILABLE else "none",
    }


async def _meta_from_backend_databus(address: str, chain: str) -> dict[str, Any] | None:
    """Own backend DataBus token detail — our aggregate, own data first."""
    body = await backend_json_get(f"/api/v1/databus/token/{address}", timeout=6.0)
    if not isinstance(body, dict):
        return None
    pairs = body.get("pairs")
    if not isinstance(pairs, list):
        return None
    pair = _best_pair([p for p in pairs if isinstance(p, dict)])
    if pair is None:
        return None
    base = pair.get("baseToken") or {}
    url = _first_pair_image([p for p in pairs if isinstance(p, dict)])
    return _finalize_meta(
        address=address,
        chain=str(pair.get("chainId") or chain),
        name=str(base.get("name") or ""),
        symbol=str(base.get("symbol") or ""),
        price_usd=pair.get("priceUsd"),
        image_url=url,
        image_source="backend:databus" if url else "none",
    )


async def _meta_from_backend_scanner(address: str, chain: str) -> dict[str, Any] | None:
    """Own backend scanner result cache — last SENTINEL scan for this token."""
    body = await backend_json_get(f"/api/v2/scanner/token/full/{chain}/{address}", timeout=6.0)
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if not isinstance(data, dict):
        return None
    url = _first_http_value(data) or _nested_image(data)
    return _finalize_meta(
        address=address,
        chain=str(data.get("chain") or chain),
        name=str(data.get("name") or ""),
        symbol=str(data.get("symbol") or ""),
        price_usd=data.get("price_usd"),
        image_url=url,
        image_source="backend:scanner" if url else "none",
    )


async def _meta_from_dexscreener(address: str) -> dict[str, Any] | None:
    """DexScreener direct — the ``icon`` is the pair's ``info.imageUrl``."""
    body = await http_get_json(f"{DEXSCREENER}/tokens/{address}", timeout=6.0)
    if not isinstance(body, dict):
        return None
    pairs = body.get("pairs")
    if not isinstance(pairs, list):
        return None
    pairs = [p for p in pairs if isinstance(p, dict)]
    pair = _best_pair(pairs)
    if pair is None:
        return None
    meta = _pair_meta(pair)
    url = _first_pair_image(pairs)
    if url:
        meta["image_url"] = url
        meta["image_available"] = True
        meta["image_source"] = "dexscreener"
    return meta


async def _meta_from_coingecko(symbol: str) -> dict[str, Any] | None:
    """CoinGecko search — last-resort logo via ``coins[0].large``."""
    q = (symbol or "").strip()
    if not q:
        return None
    body = await http_get_json(f"{COINGECKO}/search", params={"query": q}, timeout=6.0)
    if not isinstance(body, dict):
        return None
    coins = body.get("coins")
    if not isinstance(coins, list) or not coins:
        return None
    first = coins[0]
    if not isinstance(first, dict):
        return None
    url = str(first.get("large") or first.get("small") or first.get("thumb") or "")
    if not url.startswith(("http://", "https://")):
        return None
    return {
        "name": str(first.get("name") or ""),
        "symbol": str(first.get("symbol") or ""),
        "price_usd": None,
        "image_url": url,
        "image_available": True,
        "image_source": "coingecko",
    }


def _normalized_meta(meta: dict[str, Any], *, address: str, chain: str, symbol: str) -> dict[str, Any]:
    """Apply the canonical defaults to a resolved identity block.

    The returned dict ALWAYS contains ``address``, ``chain``, ``price_usd``,
    ``image_available``, ``image_source`` and ``image_url`` (a real URL or
    the literal ``"no image available"``).
    """
    meta.setdefault("address", address)
    meta.setdefault("chain", chain)
    meta.setdefault("price_usd", None)
    meta.setdefault("image_available", False)
    meta.setdefault("image_source", "none")
    if not meta.get("image_url"):
        meta["image_url"] = NO_IMAGE_AVAILABLE
    return meta


async def resolve_token_meta(
    *,
    address: str = "",
    chain: str = "",
    symbol: str = "",
) -> dict[str, Any]:
    """Resolve the canonical token-identity block (name/symbol/chain/price/image).

    Fallback order (own data first — see module docstring):
      1. own backend DataBus token detail
      2. own backend scanner result cache
      3. DexScreener direct (``info.imageUrl``)
      4. CoinGecko search (last resort, image only)

    Caching: by address — identity (name/symbol/chain/image) is served from
    Redis for 600s and the price component for 60s (separate key, so prices
    refresh without re-resolving identity). Cache is fail-open: Redis down →
    live resolution every time. Symbol-only lookups (no address) are never
    cached.

    Returns a dict that ALWAYS contains ``image_url`` (a real URL or the
    literal ``"no image available"``), ``name``, ``symbol``, ``chain`` and
    ``price_usd``. Never raises.
    """
    address = (address or "").strip()
    chain = (chain or "").strip() or _detect_chain(address)

    if address:
        cached, price_fresh = await _cache_get_meta(address, chain)
        # Full hit: identity + fresh price → serve without any network call.
        # Identity-only hit (price expired): fall through to live resolution
        # so the price is never older than the 60s price TTL.
        if cached is not None and price_fresh:
            return _normalized_meta(cached, address=address, chain=chain, symbol=symbol)

    meta: dict[str, Any] | None = None
    if address:
        meta = await _meta_from_backend_databus(address, chain)
        if meta is None or (not meta.get("name") and not meta.get("image_available")):
            meta = await _meta_from_backend_scanner(address, chain)
        if meta is None or (not meta.get("name") and not meta.get("image_available")):
            meta = await _meta_from_dexscreener(address)
        if meta is None:
            meta = await _meta_from_coingecko(symbol)

    if meta is None:
        meta = _finalize_meta(address=address, chain=chain, symbol=symbol or "")

    # A bare CoinGecko result has no address/chain — fold the query identity in.
    meta = _normalized_meta(meta, address=address, chain=chain, symbol=symbol)

    if address:
        await _cache_set_meta(address, chain, meta)
    return meta


async def token_meta(*, address: str = "", symbol: str = "", chain: str = "") -> ToolResult:
    """Resolve a token's identity block: logo + name + symbol + chain + price.

    Every scan/token result in the bot carries this block so the answer can
    always show the token image. When no source has a logo, ``image_url`` is
    the literal string ``"no image available"`` — it is never omitted.
    """
    if not address and not symbol:
        return ToolResult(tool="token_meta", ok=False, error="address or symbol required")
    meta = await resolve_token_meta(address=address, chain=chain, symbol=symbol)
    symbols = [meta.get("symbol")] if meta.get("symbol") else []
    addresses = [meta.get("address")] if meta.get("address") else []
    return ToolResult(
        tool="token_meta",
        ok=bool(meta.get("image_available")) or bool(meta.get("name")),
        data=meta,
        symbols=[str(s) for s in symbols],
        addresses=[str(a) for a in addresses],
        source=meta.get("image_source", "none"),
    )
