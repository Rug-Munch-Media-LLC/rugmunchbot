# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/_deployer_fingerprint_queries.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""ClickHouse query layer for deployer_fingerprint (degodding wave 2026-08-14).

All ``_query_*`` functions from ``engines/deployer_fingerprint.py`` live here.
They are pure data-access helpers: sync, bounded by ``max_execution_time``,
and graceful (return ``None`` / ``[]`` / ``0``) when ClickHouse is unreachable
or the query fails. ``deployer_fingerprint.py`` re-exports every name so
``deployer_fingerprint._query_*`` references in tests and callers keep
resolving to this module.

Patch-point note: unlike ``_funding_queries`` / ``_own_bundle_queries``, the
client is NOT bound at module level. ``test_deployer_fingerprint`` patches
``deployer_fingerprint._get_ch_client`` end-to-end, so ``_client()`` resolves
the name lazily through the shim at call time to keep that patch point
authoritative.
"""

from __future__ import annotations

from typing import Any

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

# Tokens used to represent native chain currency (ETH/MATIC) in evm_transfers
_NATIVE_TOKEN_MARKERS = frozenset(
    {
        "0x0000000000000000000000000000000000000000",
        "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "0x0000000000000000000000000000000000001010",  # Polygon native
        "eth",
        "native",
        "",
    }
)

# ClickHouse query execution guard — every query is bounded
_QUERY_TIMEOUT_SECONDS = 10


def _client() -> Any | None:
    """Resolve the ClickHouse client through the shim at call time.

    ``test_deployer_fingerprint`` patches ``deployer_fingerprint._get_ch_client``
    end-to-end with a fake client; looking the name up lazily here keeps that
    patch point authoritative even though the query implementation now lives
    in this submodule.
    """
    from rugmunchbot._vendor.scanner_engines.deployer_fingerprint import _get_ch_client

    return _get_ch_client()


def _query_deployer_stats(deployer_address: str, chain: str) -> dict[str, Any] | None:
    """Aggregate deployment stats from deployer_events."""
    client = _client()
    if client is None:
        return None

    where_parts = ["deployer = %(deployer)s"]
    params: dict[str, Any] = {"deployer": deployer_address}
    if chain:
        where_parts.append("chain = %(chain)s")
        params["chain"] = chain
    where_clause = " AND ".join(where_parts)

    try:
        rows = client.execute(
            f"""
            SELECT
                count() AS total_deployments,
                uniqExact(token_address) AS tokens_deployed,
                groupUniqArray(chain) AS chains_used,
                min(block_timestamp) AS first_ts,
                max(block_timestamp) AS last_ts
            FROM wallet_memory.deployer_events
            WHERE {where_clause}
            """,  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("fingerprint_deployer_stats_failed: %s", exc)
        return None

    if not rows or not rows[0]:
        return None

    row = rows[0]
    return {
        "total_deployments": int(row[0] or 0),
        "tokens_deployed": int(row[1] or 0),
        "chains_used": sorted({str(c) for c in (row[2] or [])}),
        "first_ts": row[3],
        "last_ts": row[4],
    }


def _query_per_token_deployments(deployer_address: str, chain: str) -> list[dict[str, Any]]:
    """Return per-token deployment records from deployer_events (bounded)."""
    client = _client()
    if client is None:
        return []

    where_parts = ["deployer = %(deployer)s"]
    params: dict[str, Any] = {"deployer": deployer_address, "limit": 100}
    if chain:
        where_parts.append("chain = %(chain)s")
        params["chain"] = chain
    where_clause = " AND ".join(where_parts)

    try:
        rows = client.execute(
            f"""
            SELECT
                token_address,
                any(chain) AS chain,
                min(block_timestamp) AS deployed_ts,
                argMin(tx_hash, block_timestamp) AS deploy_tx
            FROM wallet_memory.deployer_events
            WHERE {where_clause}
            GROUP BY token_address
            ORDER BY deployed_ts DESC
            LIMIT %(limit)s
            """,  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("fingerprint_per_token_deployments_failed: %s", exc)
        return []

    tokens: list[dict[str, Any]] = []
    for row in rows:
        tokens.append(
            {
                "token_address": row[0].lower(),
                "chain": row[1],
                "deployed_ts": row[2],
                "deploy_tx": row[3],
            }
        )
    return tokens


def _query_token_dex_stats(
    tokens: list[dict[str, Any]],
    chain: str,
) -> dict[str, dict[str, Any]]:
    """Compute per-token first/last swap + count from dex_events.

    ``pair_created_ts`` is proxied by the earliest DEX swap timestamp (the
    chain-indexers persist only ``type = 'swap'`` rows). ``dex_events`` has no
    ``token_address`` column — swaps are keyed by ``token0``/``token1``, so the
    two sides are grouped separately (UNION ALL). Historical rows with empty
    token0/token1 are attributed through the pair→tokens cache (a second,
    pool-keyed query merged back via ``pool_to_tokens``).
    """
    client = _client()
    if client is None or not tokens:
        return {}

    try:
        from rugmunchbot._vendor.scanner_engines.own_dex_data.client import (
            _get_pools_for_token,
            _load_pair_cache,
        )
    except ImportError:
        # Importers available; fall back to None when the module is missing
        _get_pools_for_token = None  # type: ignore[assignment]
        _load_pair_cache = None  # type: ignore[assignment]

    token_addrs = [t["token_address"] for t in tokens]

    # Known pools per token (pair cache) + pool → tracked-token attribution so
    # pool-keyed historical rows can be merged back onto the right token.
    pools: set[str] = set()
    pool_to_tokens: dict[str, set[str]] = {}
    if _get_pools_for_token is not None and _load_pair_cache is not None:
        try:
            for t in tokens:
                t_addr = t["token_address"].lower()
                pair_cache = _load_pair_cache(t.get("chain") or chain or "")
                for pool_addr in _get_pools_for_token(pair_cache, t_addr):
                    pools.add(pool_addr)
                    pool_to_tokens.setdefault(pool_addr, set()).add(t_addr)
        except (ValueError, OSError):
            pass

    where_parts = ["type = 'swap'"]
    params: dict[str, Any] = {"tokens": tuple(token_addrs)}
    if chain:
        where_parts.append("chain = %(chain)s")
        params["chain"] = chain
    where_clause = " AND ".join(where_parts)

    result: dict[str, dict[str, Any]] = {}

    try:
        rows = client.execute(
            f"""
            SELECT
                t_addr,
                min(block_timestamp) AS first_dex_ts,
                max(block_timestamp) AS last_swap_ts,
                count() AS swap_count
            FROM (
                SELECT token0 AS t_addr, block_timestamp
                FROM wallet_memory.dex_events
                WHERE {where_clause}
                  AND token0 IN %(tokens)s
                UNION ALL
                SELECT token1 AS t_addr, block_timestamp
                FROM wallet_memory.dex_events
                WHERE {where_clause}
                  AND token1 IN %(tokens)s
            ) AS swaps_side
            GROUP BY t_addr
            """,  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("fingerprint_token_dex_stats_failed: %s", exc)
        return {}

    for row in rows:
        _merge_dex_stats(result, row[0], row[1], row[2], row[3])

    # Historical pool-matched rows (empty token0/token1): attribute via the
    # pair cache — each pool row is folded into every tracked token it hosts.
    if pools:
        try:
            pool_rows = client.execute(
                f"""
                SELECT
                    contract_address,
                    min(block_timestamp) AS first_dex_ts,
                    max(block_timestamp) AS last_swap_ts,
                    count() AS swap_count
                FROM wallet_memory.dex_events
                WHERE {where_clause}
                  AND contract_address IN %(pools)s
                GROUP BY contract_address
                """,  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
                {**params, "pools": tuple(sorted(pools))},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("fingerprint_token_dex_stats_pool_failed: %s", exc)
            pool_rows = []

        for row in pool_rows:
            for t_addr in pool_to_tokens.get(row[0].lower(), ()):
                _merge_dex_stats(result, t_addr, row[1], row[2], row[3])

    return result


def _merge_dex_stats(
    result: dict[str, dict[str, Any]],
    addr: Any,
    first_ts: Any,
    last_ts: Any,
    count: Any,
) -> None:
    """Fold one dex_events aggregate row into the per-token result.

    Multiple rows can contribute to the same token (token0 side + token1 side +
    any pool-keyed rows): keep the earliest ``first_dex_ts``, the latest
    ``last_swap_ts``, and sum ``swap_count``.
    """
    addr_s = str(addr or "").lower()
    if not addr_s:
        return
    cur = result.setdefault(addr_s, {"first_dex_ts": None, "last_swap_ts": None, "swap_count": 0})
    if first_ts is not None and (cur["first_dex_ts"] is None or first_ts < cur["first_dex_ts"]):
        cur["first_dex_ts"] = first_ts
    if last_ts is not None and (cur["last_swap_ts"] is None or last_ts > cur["last_swap_ts"]):
        cur["last_swap_ts"] = last_ts
    cur["swap_count"] = int(cur["swap_count"] or 0) + int(count or 0)


def _query_funder(deployer_address: str, chain: str) -> dict[str, Any] | None:
    """Find the first wallet that funded the deployer (evm_transfers).

    Prefers native (ETH-like) transfers; falls back to the first ERC-20
    incoming transfer on record.
    """
    client = _client()
    if client is None:
        return None

    where_parts = ["to_address = %(deployer)s"]
    params: dict[str, Any] = {"deployer": deployer_address}
    if chain:
        where_parts.append("chain = %(chain)s")
        params["chain"] = chain
    where_clause = " AND ".join(where_parts)

    # ── Preferred: native currency transfer (ETH / MATIC marker) ──
    native = _query_first_incoming(client, where_clause, params, native_only=True)
    if native:
        return native

    # ── Fallback: any first incoming transfer ──
    return _query_first_incoming(client, where_clause, params, native_only=False)


def _query_first_incoming(
    client: Any,
    where_clause: str,
    params: dict[str, Any],
    native_only: bool,
) -> dict[str, Any] | None:
    """Single first-incoming-transfer query (native filter optional)."""
    try:
        if native_only:
            rows = client.execute(
                f"""
                SELECT
                    from_address,
                    min(block_timestamp) AS first_ts,
                    argMin(tx_hash, block_timestamp) AS first_tx,
                    argMin(token_address, block_timestamp) AS first_token
                FROM wallet_memory.evm_transfers
                WHERE {where_clause}
                  AND lower(token_address) IN %(native_markers)s
                GROUP BY from_address
                ORDER BY first_ts ASC
                LIMIT 1
                """,  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
                {**params, "native_markers": tuple(_NATIVE_TOKEN_MARKERS)},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
        else:
            rows = client.execute(
                f"""
                SELECT
                    from_address,
                    min(block_timestamp) AS first_ts,
                    argMin(tx_hash, block_timestamp) AS first_tx,
                    argMin(token_address, block_timestamp) AS first_token
                FROM wallet_memory.evm_transfers
                WHERE {where_clause}
                GROUP BY from_address
                ORDER BY first_ts ASC
                LIMIT 1
                """,  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
                params,
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("fingerprint_funder_query_failed: %s", exc)
        return None

    if not rows:
        return None

    row = rows[0]
    return {
        "from_address": row[0].lower(),
        "first_ts": row[1],
        "first_tx": row[2],
        "first_token": (row[3] or "").lower() if row[3] else None,
        "is_native": native_only,
    }


def _query_funder_label(funder_address: str) -> list[dict[str, Any]]:
    """Look up wallet_labels for the funder (CEX / entity classification)."""
    client = _client()
    if client is None:
        return []

    try:
        rows = client.execute(
            """
            SELECT address, chain_id, label_name, label_category, label_subtype, source
            FROM wallet_memory.wallet_labels
            WHERE address = %(addr)s
            ORDER BY loaded_at DESC
            LIMIT 20
            """,
            {"addr": funder_address},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("fingerprint_funder_label_failed: %s", exc)
        return []

    labels: list[dict[str, Any]] = []
    for row in rows:
        labels.append(
            {
                "address": row[0],
                "chain_id": row[1],
                "label_name": row[2] or "",
                "label_category": row[3] or "",
                "label_subtype": row[4] or "",
                "source": row[5] or "",
            }
        )
    return labels


def _query_funder_tx_count(funder_address: str) -> int | None:
    """Count total transactions for the funder (bounded at 11 for freshness)."""
    client = _client()
    if client is None:
        return None

    try:
        rows = client.execute(
            """
            SELECT count()
            FROM (
                SELECT 1 AS x
                FROM wallet_memory.evm_transfers
                WHERE from_address = %(addr)s OR to_address = %(addr)s
                LIMIT 11
            )
            """,
            {"addr": funder_address},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("fingerprint_funder_tx_count_failed: %s", exc)
        return None

    return int(rows[0][0]) if rows and rows[0] else None


def _query_same_funder_deployers(funder_address: str, chain: str) -> int:
    """Count known deployers funded by the same wallet (2+ = multiple)."""
    client = _client()
    if client is None:
        return 0

    try:
        deployer_rows = client.execute(
            """
            SELECT DISTINCT deployer
            FROM wallet_memory.deployer_events
            {chain_clause}

            LIMIT 5000
            """.format(  # nosec B608 - values bound via %(chain)s; only code-literal chain_clause interpolated
                chain_clause="WHERE chain = %(chain)s" if chain else ""
            ),  # where_clause/chain_clause built from code literals; values parameterized
            {"chain": chain} if chain else {},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("fingerprint_same_funder_deployer_list_failed: %s", exc)
        return 0

    if not deployer_rows:
        return 0
    deployers = tuple({r[0].lower() for r in deployer_rows})
    if len(deployers) > 5000:
        deployers = tuple(list(deployers)[:5000])

    try:
        rows = client.execute(
            """
            SELECT count(DISTINCT to_address) AS n
            FROM wallet_memory.evm_transfers
            WHERE from_address = %(funder)s
              AND to_address != %(funder)s
              AND to_address IN %(deployers)s
            """,
            {"funder": funder_address, "deployers": deployers},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("fingerprint_same_funder_overlap_failed: %s", exc)
        return 0

    return int(rows[0][0]) if rows and rows[0] else 0
