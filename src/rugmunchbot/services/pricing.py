"""Shared price/quote service for RugMunchBot — ONE fallback chain.

Before this module, the price/quote fallback chains were triplicated across
``tools/price.py`` (``price_lookup``), ``tools/market.py`` (the
CoinGecko/CoinPaprika/CoinCap quote helpers) and ``handlers/price.py``
(``cmd_price`` / ``cmd_dex`` with their own ``_own_dex_pairs`` /
``_external_dex_pairs`` resolvers). All of them now call this single service.

Public API
----------
* :func:`get_price` — THE single entry point: ``get_price(symbol_or_address,
  chain)`` auto-detects an address vs a symbol and runs the fallback chain.
* :func:`resolve` — address/symbol-aware variant for callers (like the
  ``price_lookup`` tool) that keep separate ``address``/``symbol`` kwargs;
  the address chain wins, the symbol chain runs only when the address chain
  found nothing.
* :class:`PriceResult` — the consistent result contract
  (``price_usd`` / ``change_24h`` / ``liquidity`` / ``source`` plus the raw
  ``pairs`` / ``quote`` payloads the tool wrappers need).
* ``coingecko_quote`` / ``coinpaprika_quote`` / ``coincap_quote`` /
  ``symbol_quote_fallback`` — the CEX quote helpers (moved verbatim from
  ``tools/market.py``; kept importable there as a thin shim).

Fallback chain (same sources as the legacy triplication, unified):

  address: scanner cache → backend DataBus ``/api/v1/databus/token`` →
           DexScreener ``/tokens/{address}`` → Birdeye ``/defi/price``
  symbol:  DexScreener ``/search`` (resolves symbol → address) →
           CoinGecko → CoinPaprika → CoinCap

Every number in a :class:`PriceResult` comes from one of those live sources.
If ALL sources fail the result has ``price_usd == 0`` / ``ok is False`` — the
chat layer must say "I don't have live data for that right now".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domains.telegram.rugmunchbot.config import COINGECKO, DEXSCREENER, _get_token
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_json
from app.domains.telegram.rugmunchbot.tools.birdeye import birdeye_price
from app.domains.telegram.rugmunchbot.tools.registry import backend_json_get

logger = get_logger("rmi_bot.services.pricing")

COINPAPRIKA = "https://api.coinpaprika.com/v1"
COINCAP = "https://api.coincap.io/v2"

# Stable source labels — callers render these verbatim (kept identical to the
# legacy ``price_lookup`` labels so tool payloads / chat answers don't change).
SOURCE_SCANNER = "scanner:/api/v2/scanner/token/price"
SOURCE_BACKEND = "backend:/api/v1/databus/token"
SOURCE_DEXSCREENER_TOKEN = "dexscreener:/tokens/{address}"  # noqa: S105 — source label, not a credential
SOURCE_DEXSCREENER_SEARCH = "dexscreener:/search"
SOURCE_BIRDEYE = "birdeye:/defi/price"


@dataclass
class PriceResult:
    """Consistent price/quote result from the shared fallback chain.

    Attributes:
        price_usd: Best available USD price (0.0 when no source had one).
        change_24h: 24h percent change when the source provides it.
        liquidity: USD liquidity when the source provides it.
        source: Exact source label (e.g. ``"dexscreener:/tokens/{address}"``).
        symbol / name: Token identity when the source provides it.
        volume_24h / market_cap: Optional extra market data.
        chain: Chain the price is for (as requested / detected).
        address: Resolved contract address (symbol→address resolution).
        pairs: RAW DexScreener-shaped pair list — ``cmd_dex`` renders these
            keys directly (dexId/chainId/priceUsd/liquidity/volume/pairAddress).
        quote: RAW provider quote dict when a Birdeye / CEX quote won.
    """

    price_usd: float = 0.0
    change_24h: float | None = None
    liquidity: float | None = None
    source: str = "unavailable"
    symbol: str | None = None
    name: str | None = None
    volume_24h: float | None = None
    market_cap: float | None = None
    chain: str = ""
    address: str | None = None
    pairs: list[dict[str, Any]] | None = None
    quote: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        """True when a live source produced a positive price."""
        return self.price_usd > 0

    def to_dict(self) -> dict[str, Any]:
        """Flat snapshot for the /price renderer + the Redis price cache.

        Keeps the keys ``cmd_price`` renders (``price_usd`` / ``change_24h`` /
        ``volume_24h`` / ``source``) plus ``liquidity`` — the unified contract.
        """
        return {
            "price_usd": self.price_usd,
            "change_24h": self.change_24h,
            "volume_24h": self.volume_24h,
            "liquidity": self.liquidity,
            "source": self.source,
        }


def _to_float(v: Any) -> float:
    """Coerce a source value to float; 0.0 on None / non-numeric."""
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _coingecko_headers() -> dict[str, str]:
    """CoinGecko demo-key header when the gopass/env key is present."""
    key = _get_token("rmi/infra/coingecko_api_key", ["COINGECKO_API_KEY"])
    return {"x-cg-demo-api-key": key} if key else {}


# ── CEX quote helpers (last layers of the symbol fallback chain) ────────────


async def coingecko_quote(symbol: str) -> dict[str, Any] | None:
    """CoinGecko price quote for a $SYMBOL (search → coin detail)."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    search = await http_get_json(
        f"{COINGECKO}/search", params={"query": sym}, headers=_coingecko_headers(), timeout=6.0
    )
    coins = search.get("coins") if search else None
    if not isinstance(coins, list):
        return None
    match = next((c for c in coins if isinstance(c, dict) and (c.get("symbol") or "").upper() == sym), None)
    if not match:
        return None
    coin_id = match.get("id")
    if not coin_id:
        return None
    detail = await http_get_json(
        f"{COINGECKO}/coins/{coin_id}",
        params={"localization": "false", "tickers": "false", "market_data": "true"},
        headers=_coingecko_headers(),
        timeout=6.0,
    )
    if not detail:
        return None
    raw_md = detail.get("market_data")
    md = raw_md if isinstance(raw_md, dict) else {}
    return {
        "symbol": sym,
        "name": detail.get("name") or match.get("name"),
        "price_usd": (md.get("current_price") or {}).get("usd"),
        "change_24h": md.get("price_change_percentage_24h"),
        "volume_24h": (md.get("total_volume") or {}).get("usd"),
        "market_cap": (md.get("market_cap") or {}).get("usd"),
        "source": "coingecko",
    }


async def coinpaprika_quote(symbol: str) -> dict[str, Any] | None:
    """CoinPaprika price quote for a $SYMBOL (search → ticker)."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    search = await http_get_json(f"{COINPAPRIKA}/search", params={"q": sym, "c": "currencies"}, timeout=6.0)
    items = search if isinstance(search, list) else (search or {}).get("currencies")
    if not isinstance(items, list):
        return None
    match = next((c for c in items if isinstance(c, dict) and (c.get("symbol") or "").upper() == sym), None)
    coin_id = (match or {}).get("id")
    if not coin_id:
        return None
    ticker = await http_get_json(f"{COINPAPRIKA}/tickers/{coin_id}", timeout=6.0)
    if not ticker:
        return None
    usd = ticker.get("quotes", {}).get("USD") if isinstance(ticker.get("quotes"), dict) else {}
    return {
        "symbol": sym,
        "name": ticker.get("name"),
        "price_usd": usd.get("price"),
        "change_24h": usd.get("percent_change_24h"),
        "volume_24h": usd.get("volume_24h"),
        "market_cap": usd.get("market_cap"),
        "source": "coinpaprika",
    }


async def coincap_quote(symbol: str) -> dict[str, Any] | None:
    """CoinCap price quote for a $SYMBOL (assets search)."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    body = await http_get_json(f"{COINCAP}/assets", params={"search": sym.lower()}, timeout=6.0)
    assets = body.get("data") if body else None
    if not isinstance(assets, list):
        return None
    match = next((a for a in assets if isinstance(a, dict) and (a.get("symbol") or "").upper() == sym), None)
    if not match:
        return None
    price = match.get("priceUsd")
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None
    return {
        "symbol": sym,
        "name": match.get("name"),
        "price_usd": price,
        "change_24h": match.get("changePercent24Hr"),
        "volume_24h": match.get("volumeUsd24Hr"),
        "market_cap": match.get("marketCapUsd"),
        "source": "coincap",
    }


async def symbol_quote_fallback(symbol: str) -> dict[str, Any] | None:
    """Rotate through CoinGecko → CoinPaprika → CoinCap until one returns data."""
    for fetcher in (coingecko_quote, coinpaprika_quote, coincap_quote):
        try:
            quote = await fetcher(symbol)
        except (TimeoutError, OSError) as exc:
            logger.debug("symbol fallback %s failed: %s", fetcher.__name__, exc)
            quote = None
        if quote and quote.get("price_usd") is not None:
            return quote
    return None


# ── Raw payload extraction (no normalization — callers shape their payload) ─


def _raw_pairs(body: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract the RAW DexScreener-shaped pair list from a JSON body.

    Returns only dict entries — crash-safe, ``None``/junk results dropped.
    """
    if not isinstance(body, dict):
        return []
    raw = body.get("pairs")
    if not isinstance(raw, list):
        return []
    return [p for p in raw if isinstance(p, dict)]


def _raw_pairs_from_backend(body: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract RAW pairs from the DataBus ``/token/{address}`` payload.

    Mirrors the legacy nesting (some payloads wrap pairs under ``data``) but
    keeps the RAW pair dicts — normalization happens in the tool wrapper.
    """
    if not isinstance(body, dict):
        return []
    direct = _raw_pairs(body)
    if direct:
        return direct
    data = body.get("data")
    if isinstance(data, dict):
        return _raw_pairs(data)
    return []


# ── Own-data / external address primaries ───────────────────────────────────


async def _scanner_snapshot_pairs(address: str, chain: str) -> list[dict[str, Any]] | None:
    """Own-data primary: backend scanner-cache snapshot shaped like a DEX pair.

    Returns ``None`` when the scanner cache has no entry so the chain falls
    back to the DataBus aggregate / DexScreener. Mirrors the legacy
    ``handlers.price._own_dex_pairs`` (the pair carries ``dexId="scanner"``;
    ``baseToken.address`` is added so symbol→address resolution keeps working).
    """
    chain_id = chain if chain and chain != "unknown" else "ethereum"
    body = await backend_json_get(f"/api/v2/scanner/token/price/{chain_id}/{address}", timeout=5.0)
    if not body or not body.get("cached") or not body.get("data"):
        return None
    d = body["data"]
    if not isinstance(d, dict):
        return None
    price = _to_float(d.get("price_usd"))
    if price <= 0:
        return None
    return [
        {
            "dexId": "scanner",
            "chainId": d.get("chain", chain_id),
            "priceUsd": price,
            "liquidity": {"usd": _to_float(d.get("liquidity_usd"))},
            "volume": {"h24": _to_float(d.get("volume_24h_usd"))},
            "marketCap": _to_float(d.get("market_cap_usd")),
            "pairAddress": address,
            "baseToken": {"name": d.get("name", "Unknown"), "symbol": d.get("symbol", "???"), "address": address},
        }
    ]


def _result_from_pairs(pairs: list[dict[str, Any]], *, address: str, chain: str, source: str) -> PriceResult:
    """Build a PriceResult from the FIRST raw pair (best price/liquidity)."""
    first = pairs[0]
    raw_base = first.get("baseToken")
    base = raw_base if isinstance(raw_base, dict) else {}
    raw_pc = first.get("priceChange")
    pc = raw_pc if isinstance(raw_pc, dict) else None
    raw_liq = first.get("liquidity")
    liq = raw_liq if isinstance(raw_liq, dict) else None
    raw_vol = first.get("volume")
    vol = raw_vol if isinstance(raw_vol, dict) else None
    resolved_address = address or str(base.get("address") or "")
    return PriceResult(
        price_usd=_to_float(first.get("priceUsd")),
        change_24h=_to_float(pc.get("h24")) if pc is not None and pc.get("h24") is not None else None,
        liquidity=_to_float(liq.get("usd")) if liq is not None and liq.get("usd") is not None else None,
        volume_24h=_to_float(vol.get("h24")) if vol is not None and vol.get("h24") is not None else None,
        market_cap=_to_float(first.get("marketCap")) if first.get("marketCap") is not None else None,
        symbol=base.get("symbol"),
        name=base.get("name"),
        chain=chain or str(base.get("chainId") or ""),
        address=resolved_address,
        source=source,
        pairs=pairs,
    )


# ── The two chains ──────────────────────────────────────────────────────────


async def _resolve_address(address: str, chain: str = "") -> PriceResult:
    """Address chain: scanner cache → backend DataBus → DexScreener → Birdeye.

    Every source is probed only when the previous one missed; the first
    source with usable data wins (``result.source`` carries its label).
    """
    # 1. Our scanner-cache snapshot (own data first — mirrors the legacy
    #    ``cmd_dex`` own-data primary).
    scanner_pairs = await _scanner_snapshot_pairs(address, chain)
    if scanner_pairs:
        return _result_from_pairs(scanner_pairs, address=address, chain=chain, source=SOURCE_SCANNER)
    # 2. Our backend DataBus aggregate (DexScreener + Birdeye + GMGN + scanners).
    body = await backend_json_get(f"/api/v1/databus/token/{address}", timeout=6.0)
    pairs = _raw_pairs_from_backend(body)
    if pairs:
        return _result_from_pairs(pairs, address=address, chain=chain, source=SOURCE_BACKEND)
    # 3. Direct DexScreener token endpoint (address is exact).
    body = await http_get_json(f"{DEXSCREENER}/tokens/{address}", timeout=6.0)
    pairs = _raw_pairs(body)
    if pairs:
        return _result_from_pairs(pairs, address=address, chain=chain, source=SOURCE_DEXSCREENER_TOKEN)
    # 4. Birdeye direct price (key from env/gopass; rotates on failure).
    quote = await birdeye_price(address, chain or "solana")
    if quote and quote.get("price_usd") is not None:
        return PriceResult(
            price_usd=_to_float(quote.get("price_usd")),
            source=SOURCE_BIRDEYE,
            chain=chain or "solana",
            address=address,
            quote=quote,
        )
    return PriceResult(chain=chain or "solana", address=address)


async def _resolve_symbol(symbol: str, chain: str = "") -> PriceResult:
    """Symbol chain: DexScreener search (resolves symbol → address) →
    CoinGecko → CoinPaprika → CoinCap."""
    q = (symbol or "").strip().upper()
    if not q:
        return PriceResult(chain=chain)
    body = await http_get_json(f"{DEXSCREENER}/search", params={"q": q}, timeout=6.0)
    pairs = _raw_pairs(body)
    if pairs:
        return _result_from_pairs(pairs, address="", chain=chain, source=SOURCE_DEXSCREENER_SEARCH)
    # CoinGecko → CoinPaprika → CoinCap rotation for $SYMBOL queries.
    quote = await symbol_quote_fallback(q)
    if quote and quote.get("price_usd") is not None:
        return PriceResult(
            price_usd=_to_float(quote.get("price_usd")),
            change_24h=_to_float(quote.get("change_24h")) if quote.get("change_24h") is not None else None,
            volume_24h=_to_float(quote.get("volume_24h")) if quote.get("volume_24h") is not None else None,
            market_cap=_to_float(quote.get("market_cap")) if quote.get("market_cap") is not None else None,
            symbol=quote.get("symbol") or q,
            name=quote.get("name"),
            chain=chain,
            source=str(quote.get("source") or "fallback"),
            quote=quote,
        )
    return PriceResult(chain=chain)


# ── Public API ──────────────────────────────────────────────────────────────


def _looks_like_address(query: str) -> bool:
    """True when the query is a contract address (EVM hex or Solana base58)."""
    q = query.strip()
    if q.startswith(("0x", "0X")):
        return len(q) >= 40
    # Solana base58 public keys are 32-44 alphanumeric characters.
    return 32 <= len(q) <= 44 and q.isalnum() and not q.isdigit()


async def get_price(symbol_or_address: str, chain: str = "") -> PriceResult:
    """THE single price/quote entry point.

    ``symbol_or_address`` is auto-detected: EVM hex (``0x…``, ≥40 chars) and
    Solana base58 keys (32-44 chars) take the address chain; anything else is
    treated as a symbol. ``chain`` is a hint used by the own-data / Birdeye
    steps (per-source defaults preserved, e.g. solana for Birdeye).

    Runs the fallback chain (DexScreener — via the own-data primaries — →
    CoinGecko → CoinPaprika → CoinCap). Returns a :class:`PriceResult` —
    never raises.
    """
    query = (symbol_or_address or "").strip()
    if not query:
        return PriceResult(chain=chain)
    if _looks_like_address(query):
        return await _resolve_address(query, chain)
    return await _resolve_symbol(query, chain)


async def resolve(*, address: str = "", symbol: str = "", chain: str = "") -> PriceResult:
    """Address/symbol-aware resolution for callers with separate kwargs.

    The address chain wins when ``address`` is given (exact match); the
    symbol chain runs only when the address chain found nothing usable —
    mirrors the ``price_lookup`` tool contract of trying the address path
    first and falling through to the symbol path.
    """
    if address:
        result = await _resolve_address(address, chain)
        if result.pairs or (result.quote is not None and result.quote.get("price_usd") is not None):
            return result
    if symbol:
        return await _resolve_symbol(symbol, chain)
    return PriceResult(chain=chain)


__all__ = [
    "COINCAP",
    "COINPAPRIKA",
    "PriceResult",
    "coincap_quote",
    "coingecko_quote",
    "coinpaprika_quote",
    "get_price",
    "resolve",
    "symbol_quote_fallback",
]
