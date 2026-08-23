"""Bot-native Solana on-chain resolver — the always-available data layer.

Fills the scan fields that upstream providers drop (holders, mint/freeze
authority, deployer identity, Token-2022 taxes/extensions, launch bundling)
directly from Solana RPC through ``rpc_router``'s multi-endpoint fallback
chain (public endpoints first, Helius when ``HELIUS_API_KEY`` is set).

Design contract (mirrors the scan pipeline's fail-open rules):

* every resolver is best-effort — any failure degrades to ``None`` fields,
  never raises into the scan;
* a hard per-call budget keeps public-RPC rate limits safe;
* values are REAL on-chain facts — nothing is fabricated, an unknown stays
  unknown so the formatter can render "—" honestly;
* results attach a provenance block (``result["onchain"]``) so tests and
  the formatter can tell bot-resolved facts from provider-reported ones.

SPL mint-account layout parsed here (base64 from ``getAccountInfo``)::

    [0:4]   mint_authority_option  u32 LE (0 = None → renounced)
    [4:36]  mint_authority        32-byte pubkey
    [36:44] supply                u64 LE
    [44]    decimals              u8
    [45]    is_initialized        bool
    [46:50] freeze_authority_option u32 LE
    [50:82] freeze_authority      32-byte pubkey

Token-2022 mints append a TLV extension area after byte 82; this module
walks it for the flags that matter to risk scanning (transfer fees =
Solana "taxes", permanent delegate, mint-close authority, default-frozen
state, non-transferable, transfer hook).
"""

from __future__ import annotations

import asyncio
import base64
import os
from typing import Any

import base58

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.tools.scanner.solana_onchain")

# SPL Token programs (account owner field of the mint account) — on-chain
# program addresses, not credentials (S105 false positive).
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  # noqa: S105
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"  # noqa: S105

# Token-2022 extension type ids we surface as risk-relevant flags.
_T22_EXTENSION_NAMES: dict[int, str] = {
    1: "transfer_fee",
    3: "mint_close_authority",
    6: "default_account_state",
    9: "non_transferable",
    12: "permanent_delegate",
    18: "transfer_hook_account",
    19: "transfer_hook",
}

# Hard budget for the whole enrichment pass (seconds). Public RPCs rate-limit
# aggressively; the resolver must never dominate a scan's latency.
# Hard budget for the light core resolvers (authority facts, holders,
# deployer) — kept separate from the bundle budget so heavy transaction
# fetching can never starve the high-value cheap calls.
_CORE_BUDGET_S = 8.0

# Separate, larger budget for the same-slot bundle scan (up to 30 tx fetches
# through rate-limited public RPC).
_BUNDLE_BUDGET_S = 14.0

# Same-slot bundle analysis caps (public-RPC friendly).
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


async def _birdeye_holder_count(mint: str) -> int | None:
    """Total holder count from Birdeye ``token_overview`` (key-gated).

    Free RPC can rank the largest accounts but cannot cheaply COUNT holders;
    Birdeye reports the exact number. Returns ``None`` when no
    ``BIRDEYE_API_KEY`` is configured or the lookup fails — never raises.
    """
    key = os.getenv("BIRDEYE_API_KEY", "").strip()
    if not key:
        return None
    try:
        from rugmunchbot.tools._client import http_get_json

        body = await http_get_json(
            "https://public-api.birdeye.so/defi/token_overview",
            params={"address": mint},
            headers={"X-API-KEY": key, "x-chain": "solana"},
            timeout=6.0,
        )
        data = body.get("data") if isinstance(body, dict) else None
        raw = data.get("holder_count") if isinstance(data, dict) else None
        return int(float(raw)) if raw is not None else None
    except Exception:
        logger.debug("birdeye_holder_count_failed for %s", mint)
        return None


async def resolve_holder_stats(mint: str) -> dict[str, Any] | None:
    """Top-holder concentration straight from RPC (always available).

    ``getTokenLargestAccounts`` returns the 20 largest token accounts;
    combined with ``getTokenSupply`` this yields exact top-10/top-20 supply
    concentration plus the ranked holder rows. Total holder COUNT comes from
    Birdeye when ``BIRDEYE_API_KEY`` is set — this function never invents a
    count it did not receive.
    """
    largest, supply = await asyncio.gather(
        _rpc("getTokenLargestAccounts", [mint], timeout=6.0),
        _rpc("getTokenSupply", [mint], timeout=6.0),
    )
    accounts = (
        ((largest or {}).get("result", {}).get("value") or []) if isinstance(largest, dict) else []
    )
    # Rate-limited providers can return a SUCCESSFUL response with an empty
    # account list (not an error — so endpoint rotation never triggers).
    # Retry once after a short backoff before giving up.
    if not accounts:
        await asyncio.sleep(1.5)
        largest = await _rpc("getTokenLargestAccounts", [mint], timeout=6.0)
        accounts = (
            ((largest or {}).get("result", {}).get("value") or [])
            if isinstance(largest, dict)
            else []
        )
    supply_ui = (
        float(((supply or {}).get("result", {}).get("value") or {}).get("uiAmount") or 0)
        if isinstance(supply, dict)
        else 0.0
    )
    if not accounts or supply_ui <= 0:
        return None

    holders: list[dict[str, Any]] = []
    for acct in accounts[:20]:
        addr = acct.get("address") or ""
        ui = acct.get("uiAmount")
        try:
            amount = float(ui or 0)
        except (TypeError, ValueError):
            continue
        if not addr or amount <= 0:
            continue
        holders.append({"address": addr, "pct": round(amount / supply_ui * 100, 2)})
    if not holders:
        return None
    top10 = round(sum(h["pct"] for h in holders[:10]), 2)
    top20 = round(sum(h["pct"] for h in holders[:20]), 2)
    stats: dict[str, Any] = {
        "top_holders": holders,
        "top_10_pct": top10,
        "top_20_pct": top20,
        "source": "rpc:getTokenLargestAccounts",
    }
    holder_count = await _birdeye_holder_count(mint)
    if holder_count is not None:
        stats["holder_count"] = holder_count
    return stats


async def resolve_deployer(mint: str) -> str | None:
    """Deployer identity from the mint's earliest reachable transaction.

    Walks ``getSignaturesForAddress`` back to the oldest signature in the
    window and reads the creation transaction's fee payer — the wallet that
    paid to create the mint (for pump.fun-style launches this is the human
    creator even when mint authority sits with the factory program).
    """
    sigs_resp = await _rpc("getSignaturesForAddress", [mint, {"limit": 1000}], timeout=8.0)
    sigs = (sigs_resp or {}).get("result") if isinstance(sigs_resp, dict) else None
    if not isinstance(sigs, list) or not sigs:
        return None
    oldest = sigs[-1]
    signature = oldest.get("signature") if isinstance(oldest, dict) else None
    if not signature:
        return None
    tx_resp = await _rpc(
        "getTransaction",
        [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        timeout=8.0,
    )
    tx = (tx_resp or {}).get("result") if isinstance(tx_resp, dict) else None
    if not isinstance(tx, dict):
        return None
    message = tx.get("transaction", {}).get("message", {})
    keys = message.get("accountKeys") or []
    # jsonParsed rows carry {"pubkey","signer","writable"}; raw rows are strings.
    for key in keys:
        if isinstance(key, dict) and key.get("signer"):
            return key.get("pubkey") or None
    if keys and isinstance(keys[0], str):
        return keys[0]
    header = message.get("header", {})
    if isinstance(header, dict) and (header.get("numRequiredSignatures") or 0) >= 1:
        static_keys = message.get("accountKeys") or []
        if static_keys and isinstance(static_keys[0], dict):
            return static_keys[0].get("pubkey")
    return None


def _buyers_from_tx(tx: dict[str, Any], mint: str) -> list[str]:
    """Wallets whose token balance for ``mint`` increased in this tx."""
    buyers: set[str] = set()
    meta = tx.get("meta") or {}
    pre_by_index: dict[Any, float] = {}
    for bal in meta.get("preTokenBalances") or []:
        if not isinstance(bal, dict) or (bal.get("mint") or "") != mint:
            continue
        try:
            pre_by_index[bal.get("accountIndex")] = float(
                (bal.get("uiTokenAmount") or {}).get("uiAmount") or 0
            )
        except (TypeError, ValueError):
            continue
    for bal in meta.get("postTokenBalances") or []:
        if not isinstance(bal, dict) or (bal.get("mint") or "") != mint:
            continue
        try:
            after = float((bal.get("uiTokenAmount") or {}).get("uiAmount") or 0)
        except (TypeError, ValueError):
            continue
        if after > pre_by_index.get(bal.get("accountIndex"), 0.0):
            owner = bal.get("owner") or ""
            if owner:
                buyers.add(owner)
    return sorted(buyers)


async def detect_same_slot_bundle(mint: str) -> dict[str, Any] | None:
    """Same-slot coordinated-buy detection (bot-native, RPC-only).

    Fetches the launch-window transactions, groups buyers by slot, and
    reports slots where ≥ ``_BUNDLE_MIN_WALLETS_PER_SLOT`` distinct wallets
    bought atomically — the fingerprint of sniper/bundler bots. Mirrors the
    backend engine's methodology but runs entirely on public RPC so it works
    even when the backend is unreachable.
    """
    sigs_resp = await _rpc(
        "getSignaturesForAddress", [mint, {"limit": _BUNDLE_MAX_TXS}], timeout=8.0
    )
    sigs = (sigs_resp or {}).get("result") if isinstance(sigs_resp, dict) else None
    if not isinstance(sigs, list) or not sigs:
        return None

    sem = asyncio.Semaphore(_BUNDLE_TX_CONCURRENCY)

    async def _fetch(signature: str) -> tuple[int, list[str]] | None:
        async with sem:
            tx_resp = await _rpc(
                "getTransaction",
                [
                    signature,
                    {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                ],
                timeout=8.0,
            )
        tx = (tx_resp or {}).get("result") if isinstance(tx_resp, dict) else None
        if not isinstance(tx, dict):
            return None
        slot = tx.get("slot")
        if not isinstance(slot, int):
            return None
        return slot, _buyers_from_tx(tx, mint)

    signatures = [
        s["signature"]
        for s in sigs[:_BUNDLE_MAX_TXS]
        if isinstance(s, dict) and isinstance(s.get("signature"), str)
    ]
    results = await asyncio.gather(
        *[_fetch(sig) for sig in signatures],
        return_exceptions=True,
    )

    by_slot: dict[int, set[str]] = {}
    for res in results:
        if isinstance(res, BaseException) or res is None:
            continue
        slot, buyers = res
        if buyers:
            by_slot.setdefault(slot, set()).update(buyers)

    clusters: list[dict[str, Any]] = [
        {
            "slot": slot,
            "wallets": sorted(wallets),
            "buyer_count": len(wallets),
        }
        for slot, wallets in by_slot.items()
        if len(wallets) >= _BUNDLE_MIN_WALLETS_PER_SLOT
    ]
    if not clusters:
        return {
            "is_bundled": False,
            "method": "same_slot_rpc",
            "slots_scanned": len(by_slot),
            "clusters": [],
        }
    clusters.sort(key=lambda c: int(c["buyer_count"]), reverse=True)
    top = clusters[0]
    all_bundle_wallets: set[str] = set()
    for c in clusters:
        all_bundle_wallets.update(c["wallets"])
    return {
        "is_bundled": True,
        "method": "same_slot_rpc",
        "slots_scanned": len(by_slot),
        "max_wallets_one_slot": top["buyer_count"],
        "total_bundle_wallets": len(all_bundle_wallets),
        "clusters": clusters[:5],
    }


async def enrich_scan_solana(result: dict[str, Any], address: str, chain: str) -> dict[str, Any]:
    """Fill provider-missing scan fields from Solana RPC (fail-open).

    Entry point wired into both scan paths. Only acts on Solana tokens.
    Fills, in priority order (never overwrites a real provider value with
    a worse one):

    * ``mintable`` / ``owner_renounced`` — from mint authority presence
    * ``freezable`` / ``can_blacklist`` — from freeze authority presence
    * ``buy_tax`` / ``sell_tax`` — Token-2022 transfer fee (SPL classic
      tokens have no protocol-level tax: reported as 0.0 once resolved)
    * ``holders`` / ``top_holders`` — RPC largest-accounts concentration
    * ``deployer_address`` — creation-tx fee payer fallback
    * ``bundle_analysis`` — same-slot cluster detection merge
    * ``onchain`` — provenance block for the formatter/tests
    """
    if not isinstance(result, dict) or not address:
        return result
    if (chain or "").lower() not in ("solana", "sol") and not address.endswith("pump"):
        return result

    try:
        # Two-tier budgeting: the light, high-value resolvers (authority
        # facts, holder concentration, deployer) get their own tight budget
        # so the heavy same-slot bundle scan (up to 30 transaction fetches)
        # can never starve them on rate-limited public RPC.
        facts, holders, deployer = await asyncio.wait_for(
            asyncio.gather(
                resolve_mint_facts(address),
                resolve_holder_stats(address),
                resolve_deployer(address),
                return_exceptions=True,
            ),
            timeout=_CORE_BUDGET_S,
        )
        try:
            bundle = await asyncio.wait_for(
                detect_same_slot_bundle(address), timeout=_BUNDLE_BUDGET_S
            )
        except Exception:
            bundle = None
    except Exception:
        return result
    for i, val in enumerate((facts, holders, deployer)):
        if isinstance(val, BaseException):
            logger.debug("solana resolver %d failed: %s", i, val)
            if i == 0:
                facts = None
            elif i == 1:
                holders = None
            elif i == 2:
                deployer = None
            else:
                bundle = None

    provenance: dict[str, Any] = {}

    if isinstance(facts, dict):
        provenance["mint_facts"] = True
        mint_active = facts.get("mint_authority") is not None
        freeze_active = facts.get("freeze_authority") is not None
        # Authority facts are ground truth — they override provider guesses.
        result["mintable"] = mint_active
        result["owner_renounced"] = not mint_active
        result["mint_authority"] = facts.get("mint_authority")
        result["freezable"] = freeze_active
        result["can_blacklist"] = freeze_active
        result["freeze_authority"] = facts.get("freeze_authority")
        fee_pct = facts.get("transfer_fee_pct")
        if fee_pct is not None:
            result["buy_tax"] = fee_pct / 100.0
            result["sell_tax"] = fee_pct / 100.0
        elif facts.get("token_program") == "spl":
            # Classic SPL tokens have no protocol-level transfer tax.
            result.setdefault("buy_tax", 0.0)
            result.setdefault("sell_tax", 0.0)
        exts = facts.get("extensions") or []
        if exts:
            result["token_extensions"] = exts
        provenance["token_program"] = facts.get("token_program")

    if isinstance(holders, dict):
        provenance["holder_stats"] = True
        if not result.get("top_holders"):
            result["top_holders"] = holders.get("top_holders") or []
        # Holder count: only fill when no provider reported one (never
        # overwrite a real provider count with ours, never fabricate).
        if holders.get("holder_count") is not None and not result.get("holders"):
            result["holders"] = int(holders["holder_count"])
        rs = result.get("risk_signals") if isinstance(result.get("risk_signals"), dict) else None
        if rs is not None and rs.get("top10_holder_pct") is None:
            rs["top10_holder_pct"] = holders.get("top_10_pct")
        oc = result.setdefault("onchain", {})
        oc["top_10_pct"] = holders.get("top_10_pct")
        oc["top_20_pct"] = holders.get("top_20_pct")
        if holders.get("holder_count") is not None:
            oc["holder_count"] = holders["holder_count"]

    if deployer and not (result.get("deployer_address") or result.get("dev_wallet")):
        result["deployer_address"] = deployer
        provenance["deployer_resolved"] = True

    # Lite Chain Indexer fallback layer: when the live RPC path could NOT
    # resolve the deployer, query LCI's ClickHouse store (indexed from its
    # own 62-endpoint free-RPC pool) for the token's deployer events.
    if not (deployer or result.get("deployer_address") or result.get("dev_wallet")):
        from rugmunchbot.tools.scanner._chain_pools import (
            lci_deployer_history,
            lci_deployer_summary,
        )

        try:
            lci_hist = await asyncio.wait_for(lci_deployer_history(address, chain), timeout=4.0)
        except Exception:
            lci_hist = None
        summary = lci_deployer_summary(lci_hist)
        if summary:
            result["deployer_history"] = summary
            provenance["deployer_source"] = "lite_chain_indexer"

    if isinstance(bundle, dict):
        existing = result.get("bundle_analysis")
        if isinstance(existing, dict) and existing.get("is_bundled"):
            # Backend already proved a bundle — keep its richer verdict.
            provenance["bundle_source"] = "backend"
        else:
            result["bundle_analysis"] = bundle
            result["bundle_detected"] = bool(bundle.get("is_bundled"))
            provenance["bundle_source"] = "same_slot_rpc"

    if provenance:
        result.setdefault("onchain", {}).update(provenance)
    return result


__all__ = [
    "detect_same_slot_bundle",
    "enrich_scan_solana",
    "resolve_deployer",
    "resolve_holder_stats",
    "resolve_mint_facts",
]
