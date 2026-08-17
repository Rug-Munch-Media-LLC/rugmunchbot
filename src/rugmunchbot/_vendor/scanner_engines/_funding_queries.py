# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/_funding_queries.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""ClickHouse query layer for funding_analysis (Batch 13 degodding).

All ``_query_*`` functions from ``funding_analysis.py`` live here. They are
pure data-access helpers: sync, bounded by ``max_execution_time``, and
graceful (return ``None`` / ``[]`` / ``False``) when ClickHouse is
unreachable or the query fails. ``funding_analysis.py`` re-exports every
name so ``fa._query_*`` references in tests and callers keep working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from datetime import datetime

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.lite_chain_indexer import _get_ch_client

logger = get_logger(__name__)

# Query timeout shared with funding_analysis (time-boxed queries).
_QUERY_TIMEOUT_SECONDS = 20

# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES (all sync, bounded by max_execution_time)
# ═══════════════════════════════════════════════════════════════════════════


def _query_top_holders(token_address: str, chain: str, limit: int) -> list[tuple[Any, ...]] | None:
    """Top-N holder addresses from evm_transfers (EVM) or solana balance changes."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        if chain == "solana":
            return cast(
                "list[tuple[Any, ...]] | None",
                client.execute(
                    """
                SELECT if(owner != '', owner, token_account) AS holder, sum(toFloat64OrZero(delta)) AS balance
                FROM wallet_memory.solana_spl_balance_changes
                WHERE mint = %(token)s
                GROUP BY holder
                HAVING balance > 0
                ORDER BY balance DESC
                LIMIT %(limit)s
                """,
                    {"token": token_address, "limit": limit},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                ),
            )
        # EVM: prefer the pre-aggregated holder_balances table — keyed
        # (chain, token_address, address), so this lookup is ms-fast.
        # The legacy path aggregates evm_transfers via UNION ALL, which
        # full-scans the chain's transfer rows (122M+ on ethereum) and blows
        # the query timeout on busy tokens. Fall back to it only when the
        # indexer has not seen the token yet (empty holder_balances).
        hb_rows = client.execute(
            """
            SELECT address, balance FROM wallet_memory.holder_balances FINAL
            WHERE chain = %(chain)s AND token_address = %(token)s AND balance > 0
            ORDER BY balance DESC
            LIMIT %(limit)s
            """,
            {"token": token_address, "chain": chain, "limit": limit},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        if hb_rows:
            return cast("list[tuple[Any, ...]] | None", hb_rows)
        return cast(
            "list[tuple[Any, ...]] | None",
            client.execute(
                """
            SELECT addr, balance FROM (
                SELECT
                    addr,
                    sum(net) AS balance
                FROM (
                    SELECT to_address AS addr, toFloat64OrZero(value) AS net
                    FROM wallet_memory.evm_transfers
                    WHERE token_address = %(token)s
                      AND chain = %(chain)s
                      AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
                    UNION ALL
                    SELECT from_address AS addr, -toFloat64OrZero(value) AS net
                    FROM wallet_memory.evm_transfers
                    WHERE token_address = %(token)s
                      AND chain = %(chain)s
                      AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000')
                )
                WHERE addr != ''
                GROUP BY addr
                HAVING sum(net) > 0
            )
            ORDER BY balance DESC
            LIMIT %(limit)s
            """,
                {"token": token_address, "chain": chain, "limit": limit},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            ),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("funding_top_holders_query_failed: %s", exc)
        return None


def _query_first_funders(holders: list[str], chain: str) -> list[tuple[Any, ...]] | None:
    """First incoming transfer per holder across ALL tokens (the funding event)."""
    client = _get_ch_client()
    if client is None or not holders:
        return None
    try:
        return cast(
            "list[tuple[Any, ...]] | None",
            client.execute(
                """
            SELECT
                to_address AS holder,
                argMin(from_address, block_timestamp) AS funder,
                argMin(tx_hash, block_timestamp) AS first_tx,
                min(block_timestamp) AS first_ts
            FROM wallet_memory.evm_transfers
            WHERE chain = %(chain)s
              AND to_address IN %(holders)s
              AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                          '0x000000000000000000000000000000000000dead')
            GROUP BY to_address
            """,
                {"holders": tuple(holders), "chain": chain},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            ),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("funding_first_funders_query_failed: %s", exc)
        return None


def _query_funder_tx_counts(funders: list[str], chain: str) -> list[tuple[Any, ...]] | None:
    """Total on-chain transfers per funder (in + out) — drives fresh-wallet detection."""
    client = _get_ch_client()
    if client is None or not funders:
        return None
    try:
        return cast(
            "list[tuple[Any, ...]] | None",
            client.execute(
                """
            SELECT addr, sum(cnt) AS total
            FROM (
                SELECT from_address AS addr, count() AS cnt
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s AND from_address IN %(funders)s
                GROUP BY from_address
                UNION ALL
                SELECT to_address AS addr, count() AS cnt
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s AND to_address IN %(funders)s
                GROUP BY to_address
            )
            GROUP BY addr
            """,
                {"funders": tuple(funders), "chain": chain},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            ),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("funding_funder_tx_counts_failed: %s", exc)
        return None


def _query_wallet_stats(address: str, chain: str) -> list[tuple[Any, ...]] | None:
    """First-seen timestamp and total transfer count for a wallet."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        return cast(
            "list[tuple[Any, ...]] | None",
            client.execute(
                """
            SELECT min(block_timestamp) AS first_seen, count() AS total
            FROM wallet_memory.evm_transfers
            WHERE chain = %(chain)s
              AND (from_address = %(addr)s OR to_address = %(addr)s)
              AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000')
              AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
            """,
                {"addr": address, "chain": chain},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            ),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("wallet_stats_query_failed: %s", exc)
        return None


def _query_wallet_first_funder(address: str, chain: str) -> list[tuple[Any, ...]] | None:
    """The wallet's first-ever incoming funder (any token on chain)."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        return cast(
            "list[tuple[Any, ...]] | None",
            client.execute(
                """
            SELECT argMin(from_address, block_timestamp) AS funder
            FROM wallet_memory.evm_transfers
            WHERE chain = %(chain)s
              AND to_address = %(addr)s
              AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                          '0x000000000000000000000000000000000000dead')
            """,
                {"addr": address, "chain": chain},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            ),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("wallet_first_funder_query_failed: %s", exc)
        return None


def _query_token_coverage_start(token_address: str, chain: str) -> datetime | None:
    """Earliest indexed transfer timestamp for this token — the data window.

    Freshness / brand-new / first-funder stats are only trustworthy when this
    window covers the token's full history (coverage start ≈ token launch).
    Late coverage — the indexer started tracking an old token recently —
    makes every holder look <30 days old. That is an indexing artifact, not
    a market signal, and callers must surface it as a coverage caveat.
    """
    client = _get_ch_client()
    if client is None:
        return None
    try:
        rows = client.execute(
            """
            SELECT min(block_timestamp)
            FROM wallet_memory.evm_transfers
            WHERE chain = %(chain)s AND token_address = %(token)s
            """,
            {"token": token_address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return rows[0][0] if rows and rows[0][0] is not None else None
    except (ValueError, RuntimeError, OSError):
        logger.debug("token_coverage_start_query_failed: %s", token_address[:12])
        return None


def _query_holder_first_buy(
    token_address: str,
    chain: str,
    holders: list[str],
) -> list[tuple[Any, ...]] | None:
    """First time each holder acquired the scanned token (their buy).

    Returns rows of ``(holder, first_buy_ts)`` — the earliest incoming
    transfer of ``token_address`` per holder. Drives the buyer-timing
    metrics in ``own_wallet_map``: fresh-wallet (wallet born within N hours
    before its buy) and recently-funded (funder → buy latency).
    """
    client = _get_ch_client()
    if client is None or not holders:
        return None
    try:
        return cast(
            "list[tuple[Any, ...]] | None",
            client.execute(
                """
            SELECT
                to_address AS holder,
                min(block_timestamp) AS first_buy_ts
            FROM wallet_memory.evm_transfers
            WHERE chain = %(chain)s
              AND token_address = %(token)s
              AND to_address IN %(holders)s
              AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                          '0x000000000000000000000000000000000000dead')
            GROUP BY to_address
            """,
                {"holders": tuple(holders), "token": token_address, "chain": chain},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            ),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("holder_first_buy_query_failed: %s", exc)
        return None


def _query_holder_first_tx_in(
    holders: list[str], chain: str, client: Any | None = None
) -> list[tuple[Any, ...]] | None:
    """First INCOMING transfer per holder (any token).

    Half of the wallet-birth lookup. The legacy combined query ran both
    directions as one UNION ALL — a double full scan of the chain's
    evm_transfers partition that exceeded the 10s query timeout. Splitting
    the directions lets the caller run them concurrently, each within its
    own timeout budget. ``client`` should be a dedicated connection
    (``_new_ch_client()``) when run concurrently; defaults to the shared
    singleton for sequential callers.
    """
    client = client or _get_ch_client()
    if client is None or not holders:
        return None
    try:
        return cast(
            "list[tuple[Any, ...]] | None",
            client.execute(
                """
            SELECT
                to_address AS addr,
                argMin(block_timestamp, block_timestamp) AS first_ts,
                argMin(token_address, block_timestamp) AS first_token,
                argMin(tx_hash, block_timestamp) AS first_tx_hash
            FROM wallet_memory.evm_transfers
            WHERE chain = %(chain)s
              AND to_address IN %(holders)s
              AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                          '0x000000000000000000000000000000000000dead')
            GROUP BY to_address
            """,
                {"holders": tuple(holders), "chain": chain},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            ),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("holder_first_tx_in_query_failed: %s", exc)
        return None


def _query_holder_first_tx_out(
    holders: list[str], chain: str, client: Any | None = None
) -> list[tuple[Any, ...]] | None:
    """First OUTGOING transfer per holder (any token) — see _query_holder_first_tx_in."""
    client = client or _get_ch_client()
    if client is None or not holders:
        return None
    try:
        return cast(
            "list[tuple[Any, ...]] | None",
            client.execute(
                """
            SELECT
                from_address AS addr,
                argMin(block_timestamp, block_timestamp) AS first_ts,
                argMin(token_address, block_timestamp) AS first_token,
                argMin(tx_hash, block_timestamp) AS first_tx_hash
            FROM wallet_memory.evm_transfers
            WHERE chain = %(chain)s
              AND from_address IN %(holders)s
              AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                        '0x000000000000000000000000000000000000dead')
            GROUP BY from_address
            """,
                {"holders": tuple(holders), "chain": chain},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            ),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("holder_first_tx_out_query_failed: %s", exc)
        return None


def _merge_first_tx_rows(
    in_rows: list[tuple[Any, ...]] | None,
    out_rows: list[tuple[Any, ...]] | None,
) -> list[tuple[Any, ...]]:
    """Merge the two first-tx directions, keeping the earliest row per holder."""
    best: dict[str, tuple[Any, ...]] = {}
    for rows in (in_rows, out_rows):
        for row in rows or []:
            if not row or not row[0]:
                continue
            addr = str(row[0]).lower()
            cur = best.get(addr)
            if cur is None or (row[1] is not None and (cur[1] is None or row[1] < cur[1])):
                best[addr] = row
    return list(best.values())


def _query_holder_first_tx(holders: list[str], chain: str) -> list[tuple[Any, ...]] | None:
    """First-ever on-chain transfer per holder (any token, in or out).

    Sequential fallback kept for tests/back-compat; the engine hot path uses
    ``_query_holder_first_tx_in`` + ``_query_holder_first_tx_out``
    concurrently via ``run_sync`` gather (see ``_compute_wallet_metrics``).

    Returns rows of ``(holder, first_ts, first_token, first_tx_hash)``. The
    ``first_token`` column drives ``first_tx_pct`` (holders whose first-ever
    tx is the scanned token) and ``first_ts`` drives the <30-day wallet-age
    metric.
    """
    return _merge_first_tx_rows(
        _query_holder_first_tx_in(holders, chain),
        _query_holder_first_tx_out(holders, chain),
    )


def _query_holder_set(token_address: str, chain: str, limit: int = 500) -> list[tuple[Any, ...]] | None:
    """Set of holder addresses for a token (net balance > 0), capped at ``limit``."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        if chain == "solana":
            return cast(
                "list[tuple[Any, ...]] | None",
                client.execute(
                    """
                SELECT DISTINCT if(owner != '', owner, token_account) AS holder
                FROM wallet_memory.solana_spl_balance_changes
                WHERE mint = %(token)s
                  AND delta > 0
                LIMIT %(limit)s
                """,
                    {"token": token_address, "limit": limit},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                ),
            )
        return cast(
            "list[tuple[Any, ...]] | None",
            client.execute(
                """
            SELECT DISTINCT to_address AS holder
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(token)s
              AND chain = %(chain)s
              AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
            LIMIT %(limit)s
            """,
                {"token": token_address, "chain": chain, "limit": limit},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            ),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("holder_set_query_failed: %s", exc)
        return None


def _query_known_scam_tokens(chain: str, limit: int) -> list[str]:
    """Recent tokens flagged scam-related from deployer_history."""
    client = _get_ch_client()
    if client is None:
        return []
    try:
        rows = client.execute(
            """
            SELECT token_address
            FROM wallet_memory.deployer_history
            WHERE chain_id = %(chain)s
              AND is_scam_related = 1
              AND token_address != ''
            ORDER BY deployed_at DESC
            LIMIT %(limit)s
            """,
            {"chain": chain, "limit": limit},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return [str(r[0] or "").lower() for r in rows if r and r[0]]
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("known_scam_tokens_query_failed: %s", exc)
        return []


def _query_token_scam_status(token_address: str, chain: str) -> bool:
    """True when a token is flagged in scam_addresses or deployer_history."""
    client = _get_ch_client()
    if client is None:
        return False
    try:
        rows = client.execute(
            """
            SELECT 1
            FROM wallet_memory.deployer_history
            WHERE token_address = %(token)s
              AND chain_id = %(chain)s
              AND is_scam_related = 1
            LIMIT 1
            """,
            {"token": token_address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        if rows:
            return True
        rows = client.execute(
            """
            SELECT 1
            FROM wallet_memory.scam_addresses
            WHERE address = %(token)s
              AND chain_id = %(chain)s
            LIMIT 1
            """,
            {"token": token_address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return bool(rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("token_scam_status_query_failed: %s", exc)
        return False
