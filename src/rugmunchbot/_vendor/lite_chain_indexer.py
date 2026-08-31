# VENDORED 2026-08-24 from rmi-backend/app/domains/databus/providers/chains/lite_chain_indexer.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Chain Indexer ClickHouse provider — reads chain-indexer data directly from ClickHouse.

Provides LOCAL-tier access to:
  - evm_transfers  — 73M+ ERC-20 transfers across 8 EVM chains
  - dex_events     — 47M+ DEX swap/pool events
  - deployer_events — deployer addresses for new token launches
  - solana_spl_balance_changes — 16M+ Solana SPL balance deltas

These are the SAME tables the chain-indexer writes to. The scanner has never
read from them — until now. This provider bridges that gap.

Architecture:
  DataBus fetch("token_transfers", ...) → ProviderChain → lite_chain_indexer provider
  DataBus fetch("token_holders", ...)   → ProviderChain → lite_chain_indexer provider
  DataBus fetch("new_launches", ...)    → ProviderChain → lite_chain_indexer provider
  DataBus fetch("solana_balance_changes", ...) → ProviderChain → lite_chain_indexer provider

All queries run asynchronously via asyncio.to_thread since clickhouse_driver is sync.
Every function returns dict | None following the DataBus provider contract.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger("databus.providers.lite_chain_indexer")

# ── Lazy-indexing demand signaling ────────────────────────────────────────
# The lite-chain-indexer gates DEX/deployer event indexing per chain on
# scanner demand (lazy indexing). When this provider queries dex_events or
# deployer_events for a chain, touch a lightweight signal file that the
# indexer polls each sync cycle:
#   {INDEXER_DEMAND_DIR}/{chain}/dex
#   {INDEXER_DEMAND_DIR}/{chain}/deployer
# A file touch avoids cross-service imports — no shared code, no RPC.
_INDEXER_DEMAND_DIR = os.getenv("INDEXER_DEMAND_DIR", "/tmp/indexer-demand")  # nosec B108 — stable cross-process signal dir, env-configurable  # noqa: S108


def _signal_indexer_demand(chain: str, event_type: str) -> None:
    """Signal the lite-chain-indexer that this chain+event type is in demand.

    Best-effort: touches ``{INDEXER_DEMAND_DIR}/{chain}/{event_type}``.
    Failures are logged at debug level and never block the scanner query.

    Args:
        chain: Chain name (e.g. ``'scroll'``).
        event_type: ``'dex'`` or ``'deployer'``.
    """
    if not chain or not event_type:
        return
    try:
        path = Path(_INDEXER_DEMAND_DIR) / chain.lower() / event_type
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
    except OSError as exc:
        logger.debug("indexer demand signal failed: %s", exc)


# ── Lazy ClickHouse client ────────────────────────────────────────────────
_ch_client: Any = None
# Thread-local client cache. ``clickhouse_driver.Client`` does NOT tolerate
# concurrent queries on a single connection ("Simultaneous queries on single
# connection detected"), and the scanner engines fan queries out across
# ``run_sync`` (asyncio ``to_thread``) worker threads — holder_clusters,
# wallet_flow, and the batch label/contract helpers all ``asyncio.gather``
# concurrent queries. Caching one client per worker thread keeps the shared
# connection sequential-safe while still reusing connections (the default
# asyncio thread pool is bounded, so at most ~cpu+4 connections are created).
# Strictly-sequential callers (the main thread) keep a single cached client.
_ch_local = threading.local()


def _new_ch_client() -> Any | None:
    """Fresh (uncached) ClickHouse connection for the wallet_memory database.

    clickhouse_driver.Client does NOT tolerate concurrent queries on one
    connection ("Simultaneous queries on single connection detected").
    Engines that fan queries out across ``run_sync`` threads must each use
    their own connection from this factory; the shared ``_get_ch_client``
    singleton is only safe for strictly-sequential callers.
    """
    ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "")
    # Fail closed: never default to an rmi-owned store. Chain-indexer data must
    # be configured explicitly (bot-local store) or routed via the backend API.
    if not ch_host:
        logger.debug("chain-indexer ClickHouse host not configured; skipping")
        return None
    ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT") or "9000")
    ch_user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
    ch_pass = os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD", "")
    ch_db = "wallet_memory"

    try:
        from clickhouse_driver import Client as CHClient
        from clickhouse_driver.errors import Error as CHDriverError
    except ImportError:
        logger.warning("clickhouse_driver not installed; Chain-indexer ClickHouse unavailable")
        return None

    try:
        candidate: Any = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass or "",  # empty string, never None (driver crashes on None)
            database=ch_db,
            connect_timeout=3,
            send_receive_timeout=20,
        )
        candidate.execute("SELECT 1")
    except (
        ConnectionError,
        TimeoutError,
        RuntimeError,
        OSError,
        AttributeError,
        CHDriverError,
    ) as e:
        logger.warning("Chain-indexer ClickHouse unavailable: %s", str(e).splitlines()[0][:200])
        return None
    return candidate


def _get_ch_client() -> Any | None:
    """Lazy per-thread ClickHouse client for the wallet_memory database.

    ``clickhouse_driver.Client`` does not tolerate concurrent queries on one
    connection, and scanner engines fan queries out across ``run_sync``
    thread-pool workers. Caching per thread (via ``threading.local``) keeps
    each connection strictly-sequential while allowing concurrency across
    threads. The legacy module-level ``_ch_client`` global is kept as a
    read-through alias of the main-thread client for compatibility.
    """
    global _ch_client
    cached = getattr(_ch_local, "client", None)
    if cached is not None:
        return cached
    candidate = _new_ch_client()
    if candidate is None:
        return None
    _ch_local.client = candidate
    if threading.current_thread() is threading.main_thread():
        _ch_client = candidate
    logger.info("Chain-indexer ClickHouse connected (thread client)")
    return _ch_local.client


# ── Async wrapper ─────────────────────────────────────────────────────────


async def _run_sync(fn, *args, **kwargs):
    """Run a sync function in a thread pool (clickhouse_driver is sync)."""
    import asyncio

    return await asyncio.to_thread(fn, *args, **kwargs)


# ── CHAIN MAP: normalize chain names to ClickHouse format ─────────────────

_CHAIN_MAP: dict[str, str] = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "mainnet": "ethereum",
    "bsc": "bsc",
    "bnb": "bsc",
    "binance": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "optimism": "optimism",
    "op": "optimism",
    "base": "base",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "fantom": "fantom",
    "ftm": "fantom",
    "gnosis": "gnosis",
    "xdai": "gnosis",
}


def _normalize_chain(chain: str) -> str:
    """Normalize chain name to ClickHouse format. Returns '' if unknown."""
    return _CHAIN_MAP.get(chain.lower(), chain.lower())


# ═══════════════════════════════════════════════════════════════════════════
# PROVIDER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════


async def _lite_chain_indexer_token_transfers(**kwargs: Any) -> dict[str, Any] | None:
    """Query evm_transfers for token transfer history.

    Parameters (from DataBus fetch kwargs):
      token_address: str — ERC-20 token contract address
      chain: str — chain name (ethereum, bsc, arbitrum, etc.)
      from_block: int — starting block (optional, default 0)
      to_block: int — ending block (optional, default unlimited)
      limit: int — max results (optional, default 100)

    Returns dict with transfer list or None.
    """
    token_address = kwargs.get("token_address", kwargs.get("address", "")).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    from_block = int(kwargs.get("from_block", 0))
    to_block = int(kwargs.get("to_block", 0))
    limit = min(int(kwargs.get("limit", 100)), 1000)

    if not token_address:
        return None
    if not chain:
        return None

    def _query() -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []

        params: dict[str, Any] = {
            "addr": token_address,
            "limit": limit,
        }

        where_parts = ["et.token_address = %(addr)s", "et.chain = %(chain)s"]
        params["chain"] = chain

        if from_block > 0:
            where_parts.append("et.block_number >= %(from_block)s")
            params["from_block"] = from_block
        if to_block > 0:
            where_parts.append("et.block_number <= %(to_block)s")
            params["to_block"] = to_block

        where_clause = " AND ".join(where_parts)

        try:
            # evm_transfers stores empty token_symbol/token_name (enrichment
            # moved to wallet_memory.token_meta_ref — one row per token), so
            # symbol/name come from a LEFT JOIN on the reference table.
            return client.execute(
                f"""
                SELECT et.chain, et.block_number, et.tx_hash, et.from_address,
                       et.to_address, et.token_address, et.value,
                       et.block_timestamp, et.log_index,
                       tm.symbol, tm.name
                FROM wallet_memory.evm_transfers AS et
                LEFT JOIN wallet_memory.token_meta_ref AS tm
                    ON tm.chain = et.chain AND tm.token_address = et.token_address
                WHERE {where_clause}
                ORDER BY et.block_number DESC, et.log_index DESC
                LIMIT %(limit)s
                """,  # noqa: S608 - verified bound/allowlisted SQL construction  # nosec B608 - where_parts are code literals; values bound via %(name)s params
                params,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("evm_transfers query failed: %s", exc)
            return []

    rows = await _run_sync(_query)
    if not rows:
        return None

    transfers = []
    for row in rows:
        transfers.append(
            {
                "chain": row[0],
                "block_number": row[1],
                "tx_hash": row[2],
                "from_address": row[3],
                "to_address": row[4],
                "token_address": row[5],
                "value": row[6],
                "block_timestamp": row[7].isoformat()
                if hasattr(row[7], "isoformat")
                else str(row[7]),
                "log_index": row[8],
                "token_symbol": row[9],
                "token_name": row[10],
            }
        )

    return {
        "data": {
            "transfers": transfers,
            "count": len(transfers),
            "token_address": token_address,
            "chain": chain,
        },
        "source": "lite_chain_indexer (ClickHouse evm_transfers)",
        "tier": "local",
        "is_local": True,
    }


async def _lite_chain_indexer_dex_events(**kwargs: Any) -> dict[str, Any] | None:
    """Query dex_events for DEX swap/pool events on a token.

    Parameters:
      token_address: str — token contract address (matched against token0/token1)
      chain: str — chain name
      limit: int — max results (optional, default 50)

    Returns dict with DEX events or None.
    """
    token_address = kwargs.get("token_address", kwargs.get("address", "")).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    limit = min(int(kwargs.get("limit", 50)), 500)

    if not token_address or not chain:
        return None

    # Signal lazy indexing: the scanner wants dex_events on this chain, so
    # the indexer should enable DEX event fetching for it.
    _signal_indexer_demand(chain, "dex")

    def _query() -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []

        params: dict[str, Any] = {
            "addr0": token_address,
            "addr1": token_address,
            "chain": chain,
            "limit": limit,
        }

        try:
            return client.execute(
                """
                SELECT type, chain, block_number, block_timestamp, tx_hash,
                       log_index, contract_address, dex, version,
                       token0, token1, sender, recipient,
                       amount0_in, amount1_in, amount0_out, amount1_out,
                       amount0, amount1, whale
                FROM wallet_memory.dex_events
                WHERE chain = %(chain)s
                  AND (token0 = %(addr0)s OR token1 = %(addr1)s)
                ORDER BY block_number DESC, log_index DESC
                LIMIT %(limit)s
                """,
                params,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("dex_events query failed: %s", exc)
            return []

    rows = await _run_sync(_query)
    if not rows:
        return None

    events = []
    for row in rows:
        events.append(
            {
                "type": row[0],
                "chain": row[1],
                "block_number": row[2],
                "block_timestamp": row[3].isoformat()
                if hasattr(row[3], "isoformat")
                else str(row[3]),
                "tx_hash": row[4],
                "log_index": row[5],
                "contract_address": row[6],
                "dex": row[7],
                "version": row[8],
                "token0": row[9],
                "token1": row[10],
                "sender": row[11],
                "recipient": row[12],
                "amount0_in": row[13],
                "amount1_in": row[14],
                "amount0_out": row[15],
                "amount1_out": row[16],
                "amount0": row[17],
                "amount1": row[18],
                "whale": bool(row[19]) if len(row) > 19 else False,
            }
        )

    return {
        "data": {
            "dex_events": events,
            "count": len(events),
            "token_address": token_address,
            "chain": chain,
        },
        "source": "lite_chain_indexer (ClickHouse dex_events)",
        "tier": "local",
        "is_local": True,
    }


async def _lite_chain_indexer_deployer_history(**kwargs: Any) -> dict[str, Any] | None:
    """Query deployer_events for deployer address history.

    Parameters:
      address: str — deployer wallet address (deployer field)
      chain: str — chain name (optional)
      limit: int — max results (optional, default 50)

    Returns dict with deployer events or None.
    """
    address = kwargs.get("address", kwargs.get("deployer", "")).lower()
    chain_raw = kwargs.get("chain", "")
    chain = _normalize_chain(chain_raw) if chain_raw else ""
    limit = min(int(kwargs.get("limit", 50)), 500)

    if not address:
        return None

    # Signal lazy indexing: the scanner wants deployer_events on this chain.
    # Only signal when a specific chain is requested — a chain-less query
    # spans every chain and cannot be attributed to one demand signal.
    if chain:
        _signal_indexer_demand(chain, "deployer")

    def _query() -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []

        params: dict[str, Any] = {
            "deployer": address,
            "limit": limit,
        }

        where_parts = ["deployer = %(deployer)s"]
        if chain:
            where_parts.append("chain = %(chain)s")
            params["chain"] = chain

        where_clause = " AND ".join(where_parts)

        try:
            return client.execute(
                f"""
                SELECT deployer, token_address, chain, block_number,
                       block_timestamp, tx_hash, type, factory
                FROM wallet_memory.deployer_events
                WHERE {where_clause}
                ORDER BY block_number DESC
                LIMIT %(limit)s
                """,  # noqa: S608 - verified bound/allowlisted SQL construction  # nosec B608 - where_parts are code literals; values bound via %(name)s params
                params,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("deployer_events query failed: %s", exc)
            return []

    rows = await _run_sync(_query)
    if not rows:
        return None

    events = []
    for row in rows:
        events.append(
            {
                "deployer": row[0],
                "token_address": row[1],
                "chain": row[2],
                "block_number": row[3],
                "block_timestamp": row[4].isoformat()
                if hasattr(row[4], "isoformat")
                else str(row[4]),
                "tx_hash": row[5],
                "type": row[6],
                "factory": row[7],
            }
        )

    return {
        "data": {
            "deployer_events": events,
            "count": len(events),
            "deployer": address,
            "chain": chain or "any",
        },
        "source": "lite_chain_indexer (ClickHouse deployer_events)",
        "tier": "local",
        "is_local": True,
    }


async def _lite_chain_indexer_solana_balance_changes(**kwargs: Any) -> dict[str, Any] | None:
    """Query solana_spl_balance_changes for SPL token balance deltas.

    Parameters:
      token_address: str — SPL token mint address
      limit: int — max results (optional, default 100)

    Returns dict with balance changes or None.
    """
    mint = kwargs.get("token_address", kwargs.get("address", kwargs.get("mint", ""))).lower()
    limit = min(int(kwargs.get("limit", 100)), 1000)

    if not mint:
        return None

    def _query() -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []

        params: dict[str, Any] = {
            "mint": mint,
            "limit": limit,
        }

        try:
            return client.execute(
                """
                SELECT slot, block_timestamp, tx_hash, token_account, mint, owner,
                       pre_amount, post_amount, delta, decimals,
                       token_symbol, token_name
                FROM wallet_memory.solana_spl_balance_changes
                WHERE mint = %(mint)s
                ORDER BY block_timestamp DESC, slot DESC
                LIMIT %(limit)s
                """,
                params,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("solana_spl_balance_changes query failed: %s", exc)
            return []

    rows = await _run_sync(_query)
    if not rows:
        return None

    changes = []
    for row in rows:
        changes.append(
            {
                "slot": row[0],
                "block_timestamp": row[1].isoformat()
                if hasattr(row[1], "isoformat")
                else str(row[1]),
                "tx_hash": row[2],
                "token_account": row[3],
                "mint": row[4],
                "owner": row[5],
                "pre_amount": row[6],
                "post_amount": row[7],
                "delta": row[8],
                "decimals": row[9],
                "token_symbol": row[10],
                "token_name": row[11],
            }
        )

    return {
        "data": {
            "balance_changes": changes,
            "count": len(changes),
            "mint": mint,
        },
        "source": "lite_chain_indexer (ClickHouse solana_spl_balance_changes)",
        "tier": "local",
        "is_local": True,
    }


async def _lite_chain_indexer_holders(**kwargs: Any) -> dict[str, Any] | None:
    """Compute token holder count from evm_transfers unique addresses.

    Parameters:
      token_address: str — ERC-20 token contract address
      chain: str — chain name
      limit: int — max holder entries (optional, default 50)

    Returns dict with top holders or None.

    NOTE: This provides approximate holder metrics by analyzing transfer
    patterns. For exact on-chain balances, use RPC-level sources.
    """
    token_address = kwargs.get("token_address", kwargs.get("address", "")).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    limit = min(int(kwargs.get("limit", 50)), 500)

    if not token_address or not chain:
        return None

    def _query() -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []

        params: dict[str, Any] = {
            "addr": token_address,
            "chain": chain,
            "limit": limit,
        }

        try:
            # Rank addresses by number of incoming transfers (proxy for holder activity)
            return client.execute(
                """
                SELECT to_address, count() AS tx_count,
                       min(block_number) AS first_block,
                       max(block_number) AS last_block
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(addr)s
                  AND chain = %(chain)s
                  AND to_address != '0x0000000000000000000000000000000000000000'
                GROUP BY to_address
                ORDER BY tx_count DESC
                LIMIT %(limit)s
                """,
                params,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("holder query failed: %s", exc)
            return []

    def _count_query() -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []

        params: dict[str, Any] = {
            "addr": token_address,
            "chain": chain,
        }

        try:
            return client.execute(
                """
                SELECT uniq(to_address) AS total_holders
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(addr)s
                  AND chain = %(chain)s
                  AND to_address != '0x0000000000000000000000000000000000000000'
                """,
                params,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("holder count query failed: %s", exc)
            return []

    # Run queries sequentially — clickhouse_driver.Client is NOT thread-safe
    rows = await _run_sync(_query)
    if not rows:
        return None

    count_rows = await _run_sync(_count_query)
    total = count_rows[0][0] if count_rows else 0

    holders_list = []
    for row in rows:
        holders_list.append(
            {
                "address": row[0],
                "tx_count": row[1],
                "first_block": row[2],
                "last_block": row[3],
            }
        )

    return {
        "data": {
            "holders": holders_list,
            "total_holders": total,
            "top_holders_count": len(holders_list),
            "token_address": token_address,
            "chain": chain,
        },
        "source": "lite_chain_indexer (ClickHouse evm_transfers aggregation)",
        "tier": "local",
        "is_local": True,
    }


async def _lite_chain_indexer_pattern_search(**kwargs: Any) -> dict[str, Any] | None:
    """Search chain-indexer for on-chain scam patterns (DeFi behavioral).

    Provides natural-language-queryable pattern detection:
      - \"show me tokens with similar deployer patterns to this one\"
      - \"show me recent rug pulls on this chain\"
      - \"what's the holder concentration for this token?\"

    Delegates to the chain_pattern_detector module which queries ClickHouse
    tables (evm_transfers, dex_events, deployer_events) and converts findings
    into structured results.

    Parameters:
      token_address: str — token address to analyze (optional)
      chain: str — chain name (optional)
      deployer: str — deployer address (optional)
      query_type: str — one of: \"auto\", \"deployer_cluster\", \"holder_concentration\",
                         \"pump_and_dump\", \"mixer_interaction\" (default: \"auto\")

    Returns dict with patterns detected or None.
    """
    token_address = kwargs.get("token_address", kwargs.get("address", "")).lower()
    chain_raw = kwargs.get("chain", "")
    chain = _normalize_chain(chain_raw) if chain_raw else ""
    deployer = kwargs.get("deployer", "").lower()
    query_type = kwargs.get("query_type", kwargs.get("q", "auto"))

    if not token_address and not deployer:
        return None

    try:
        from app.domains.scanner.modules.chain_pattern_detector import (
            detect_all_patterns,
            detect_deployer_clusters,
            detect_holder_concentration,
            detect_mixer_interactions,
            detect_pump_and_dump,
        )

        if query_type == "deployer_cluster" and deployer:
            result = await detect_deployer_clusters(deployer, chain)
            return {
                "data": result,
                "source": "lite_chain_indexer (pattern: deployer_cluster)",
                "tier": "local",
                "is_local": True,
            }

        if query_type == "holder_concentration" and token_address and chain:
            result = await detect_holder_concentration(token_address, chain)
            return {
                "data": result,
                "source": "lite_chain_indexer (pattern: holder_concentration)",
                "tier": "local",
                "is_local": True,
            }

        if query_type == "pump_and_dump" and token_address and chain:
            result = await detect_pump_and_dump(token_address, chain)
            return {
                "data": result,
                "source": "lite_chain_indexer (pattern: pump_and_dump)",
                "tier": "local",
                "is_local": True,
            }

        if query_type == "mixer_interaction" and deployer:
            result = await detect_mixer_interactions(deployer, chain)
            return {
                "data": result,
                "source": "lite_chain_indexer (pattern: mixer_interaction)",
                "tier": "local",
                "is_local": True,
            }

        # Auto-detect: run all applicable detectors
        findings = await detect_all_patterns(token_address, chain, deployer)

        # Ingest high-confidence findings into RAG for permanent scam pattern storage
        high_risk = [f for f in findings if f.get("risk_score", 0) >= 30]
        ingested = 0
        if high_risk:
            try:
                from app.domains.scanner.modules.chain_pattern_detector import (
                    ingest_patterns_into_rag,
                )

                ingested = await ingest_patterns_into_rag(high_risk)
            except ImportError:
                logger.debug("chain_pattern_rag_ingest_unavailable")
            except Exception as exc:
                logger.warning("chain_pattern_rag_ingest_failed", error=str(exc)[:120])

        return {
            "data": {
                "findings": findings,
                "match_count": len(findings),
                "total_risk_score": min(100, sum(f.get("risk_score", 0) for f in findings)),
                "ingested_into_rag": ingested,
            },
            "source": "lite_chain_indexer (pattern: auto)",
            "tier": "local",
            "is_local": True,
        }

    except ImportError as e:
        logger.debug("chain_pattern_detector not available: %s", e)
        return None
    except Exception as e:
        logger.warning("lite_chain_indexer_pattern_search failed: %s", e)
        return None
