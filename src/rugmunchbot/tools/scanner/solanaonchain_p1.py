"""_solana_onchain.py split part: logger, SPL_TOKEN_PROGRAM, TOKEN_2022_PROGRAM, _T22_EXTENSION_NAMES, _CORE_BUDGET_S, _BUNDLE_BUDGET_S, _BUNDLE_MAX_TXS, _BUNDLE_TX_CONCURRENCY, _BUNDLE_MIN_WALLETS_PER_SLOT, _router, _rpc, _rpc_rotating, _encode_pubkey, _parse_mint_layout, _CACHE_TTL_S, _cache, _cache_get, _cache_set, resolve_mint_facts."""
from __future__ import annotations
from rugmunchbot.support.logging import get_logger
from typing import Any
import asyncio
import base58
import base64

logger = get_logger("rmi_bot.tools.scanner.solana_onchain")

SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  # noqa: S105

TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"  # noqa: S105

_T22_EXTENSION_NAMES: dict[int, str] = {
    1: "transfer_fee",
    3: "mint_close_authority",
    6: "default_account_state",
    9: "non_transferable",
    12: "permanent_delegate",
    18: "transfer_hook_account",
    19: "transfer_hook",
}

_CORE_BUDGET_S = 8.0

_BUNDLE_BUDGET_S = 14.0

_BUNDLE_MAX_TXS = 30

_BUNDLE_TX_CONCURRENCY = 5

_BUNDLE_MIN_WALLETS_PER_SLOT = 2

def _router():
    """Resolve the vendored RPC router lazily (import-isolated, fail-open)."""
    try:
        from rugmunchbot._vendor.scanner_engines.rpc_router import get_rpc_router

        # Extend the frozen endpoint pools with bot-owned curated free
        # endpoints (idempotent, fail-open) so every chain keeps enough
        # healthy endpoints to always answer.
        from rugmunchbot.tools.scanner._chain_pools import install_pool_extensions

        install_pool_extensions()
        return get_rpc_router()
    except ImportError:  # pragma: no cover - vendor always ships in practice
        return None

async def _rpc(method: str, params: list[Any], timeout: float = 8.0) -> Any:
    """Single JSON-RPC call through the router; ``None`` on any failure."""
    router = _router()
    if router is None:
        return None
    try:
        return await router.call("solana", method, params, timeout=timeout)
    except Exception as exc:
        logger.debug("solana rpc %s failed: %s", method, exc)
        return None

async def _rpc_rotating(
    method: str, params: list[Any], *, attempts: int = 3, timeout: float = 6.0
) -> Any:
    """JSON-RPC call that FORCES endpoint rotation between attempts.

    The router's health tracking only marks failures — a rate-limited
    endpoint that answers ``200`` with an empty result stays "healthy" and
    wins the next pick too. This helper walks the healthy endpoint list
    itself so each attempt lands on a different provider.
    """
    import httpx

    router = _router()
    if router is None:
        return None
    try:
        endpoints = await router.ensure_healthy_endpoints("solana")
    except Exception:
        return await _rpc(method, params, timeout=timeout)
    urls = [url for url, _ in (endpoints or [])]
    if not urls:
        return await _rpc(method, params, timeout=timeout)

    last: Any = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for i, url in enumerate(urls[:attempts]):
            try:
                resp = await client.post(
                    url,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    timeout=timeout,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    result = data.get("result") if isinstance(data, dict) else None
                    if result:
                        return {"result": result}
                    last = {"result": result}
            except Exception as exc:
                logger.debug("rotating rpc %s attempt %d failed: %s", method, i + 1, exc)
            if i < min(len(urls), attempts) - 1:
                await asyncio.sleep(0.4)
    return last

def _encode_pubkey(raw: bytes) -> str | None:
    """Base58-encode a 32-byte pubkey, ``None`` for zeroed/invalid bytes."""
    if not raw or len(raw) != 32 or not any(raw):
        return None
    try:
        return base58.b58encode(raw).decode("ascii")
    except Exception:
        return None

def _parse_mint_layout(data: bytes) -> dict[str, Any]:
    """Parse the 82-byte SPL mint layout (+ Token-2022 extensions)."""
    facts: dict[str, Any] = {}
    if len(data) < 82:
        return facts
    mint_opt = int.from_bytes(data[0:4], "little")
    facts["mint_authority"] = _encode_pubkey(data[4:36]) if mint_opt == 1 else None
    facts["supply_raw"] = int.from_bytes(data[36:44], "little")
    facts["decimals"] = data[44]
    freeze_opt = int.from_bytes(data[46:50], "little")
    facts["freeze_authority"] = _encode_pubkey(data[50:82]) if freeze_opt == 1 else None

    # Token-2022 TLV extension walk (starts right after the base layout).
    extensions: set[str] = set()
    transfer_fee_bp: int | None = None
    offset = 82
    while offset + 4 <= len(data):
        ext_type = int.from_bytes(data[offset : offset + 2], "little")
        ext_len = int.from_bytes(data[offset + 2 : offset + 4], "little")
        body_start = offset + 4
        body_end = min(body_start + ext_len, len(data))
        name = _T22_EXTENSION_NAMES.get(ext_type)
        if name:
            extensions.add(name)
            if ext_type == 1 and ext_len >= 108:
                # TransferFeeConfig: 2x COption<Pubkey>(36) + withheld u64(8)
                # + older{epoch u64, max_fee u64, bp u16} + newer{same}.
                base = body_start + 80
                older_bp = int.from_bytes(data[base + 16 : base + 18], "little")
                newer_base = base + 18
                newer_bp = int.from_bytes(data[newer_base + 16 : newer_base + 18], "little")
                candidates = [bp for bp in (older_bp, newer_bp) if bp > 0]
                if candidates:
                    # Worst-case configured fee — conservative for risk scans.
                    transfer_fee_bp = max(candidates)
        offset = body_end
    if extensions:
        facts["extensions"] = sorted(extensions)
    if transfer_fee_bp is not None:
        facts["transfer_fee_pct"] = round(transfer_fee_bp / 100.0, 2)
    return facts

_CACHE_TTL_S = 120.0

_cache: dict[str, tuple[float, Any]] = {}

def _cache_get(key: str) -> Any | None:
    import time

    hit = _cache.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.monotonic() - ts > _CACHE_TTL_S:
        _cache.pop(key, None)
        return None
    return val

def _cache_set(key: str, val: Any) -> None:
    import time

    if len(_cache) > 512:  # bounded
        _cache.clear()
    _cache[key] = (time.monotonic(), val)

async def resolve_mint_facts(mint: str) -> dict[str, Any] | None:
    """Resolve mint authority / freeze authority / program / tax from RPC.

    Returns ``None`` when the account cannot be fetched (RPC down, bad
    address). A fetched-but-unparseable payload yields partial facts.
    """
    resp = await _rpc("getAccountInfo", [mint, {"encoding": "base64"}], timeout=6.0)
    value = (resp or {}).get("result", {}).get("value") if isinstance(resp, dict) else None
    if not isinstance(value, dict):
        return None
    owner_program = value.get("owner") or ""
    try:
        data = base64.b64decode(value.get("data", ["", "base64"])[0])
    except Exception:
        return None
    facts = _parse_mint_layout(data)
    if not facts:
        return None
    facts["token_program"] = "token-2022" if owner_program == TOKEN_2022_PROGRAM else "spl"
    facts["program_owner"] = owner_program
    return facts
