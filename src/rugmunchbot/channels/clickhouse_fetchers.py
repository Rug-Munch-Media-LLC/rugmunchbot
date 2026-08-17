"""Small real-data fetchers for the alpha channel — ClickHouse + smart-money feed.

Sources (all fail-open, never raise):
  * whale moves        → ``wallet_memory.token_transfers`` (own indexer; rows
                         carry ``value_decimal`` USD value), entity-labeled via
                         ``entity_registry``. Replaces the empty
                         ``WhaleWatcher._alert_history`` path.
  * early whale entries→ ``wallet_memory.deployer_events`` + first transfers:
                         wallets that entered a fresh token within the first
                         blocks of its deploy, with size (USD).
  * smart money        → ``app.databus.rugcharts_intel.smart_money_feed`` (the
                         live feed, not the dead premium HTTP endpoint).

ClickHouse access uses the chain-indexer thread-local client factory
(``lite_chain_indexer._get_ch_client``) — the same one the launches router
uses for ``deployer_events`` — and every query runs inside
``asyncio.to_thread`` so the event loop never blocks on the sync driver.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from app.databus.rugcharts_intel.smart_money import smart_money_feed
from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.clickhouse_fetchers")

#: Default minimum USD value for a "whale" transfer (matches WhaleMoveProvider).
WHALE_MIN_VALUE_USD: float = 10_000.0

#: Lookback window for the hourly whale digest.
WHALE_LOOKBACK_HOURS: int = 24

#: Early-entry window: wallets that bought within N blocks of the deploy tx.
EARLY_ENTRY_BLOCK_WINDOW: int = 100

#: Deploy lookback for early-entry candidates.
EARLY_ENTRY_LOOKBACK_HOURS: int = 48

_QUERY_TIMEOUT_SECONDS: int = 20


def _short_addr(addr: str) -> str:
    """Shorten a long address for titles/labels (``abc...xyz``)."""
    if len(addr) > 12:
        return f"{addr[:6]}...{addr[-4:]}"
    return addr


def _fmt_usd(value: Any) -> str:
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return "unknown"


def _entity_label(addr: str) -> str:
    """Best-effort entity label for an address ('' when unknown/infra)."""
    try:
        from app.entity_registry import get_entity_registry

        match = get_entity_registry().classify_address(addr)
    except Exception as exc:  # fail-open by contract
        logger.debug("channels:entity_classify_failed err=%s", str(exc)[:120])
        return ""
    if match is None:
        return ""
    return f"{match.entity_name} ({match.entity_type})"


def _is_infrastructure(addr: str) -> bool:
    """True when an address is a known exchange/DeFi/mixer (not a whale)."""
    try:
        from app.entity_registry import get_entity_registry

        return get_entity_registry().is_infrastructure(addr)
    except Exception:  # fail-open: unknown addresses pass through
        return False


# ── ClickHouse (sync driver, off the event loop) ─────────────────────────


def _run_ch_query(sql: str, params: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
    """Run one ClickHouse query on the thread-local chain-indexer client."""
    try:
        from app.domains.databus.providers.chains.lite_chain_indexer import _get_ch_client

        client = _get_ch_client()
        if client is None:
            return []
        return client.execute(sql, params or {}, settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS})
    except Exception as exc:  # fail-open by contract
        logger.debug("channels:clickhouse_query_failed err=%s", str(exc)[:200])
        return []


async def _ch_query(sql: str, params: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
    """Run a ClickHouse query without blocking the event loop."""
    return await asyncio.to_thread(_run_ch_query, sql, params)


def _chain_filter(chain: str) -> tuple[str, dict[str, Any]]:
    """Build the allowlisted chain WHERE fragment + params ('' when none)."""
    try:
        from app.domains.databus.providers.chains.lite_chain_indexer import _normalize_chain

        normalized = _normalize_chain(chain)
    except Exception:
        normalized = ""
    if not normalized:
        return "", {}
    return "AND chain_id = %(chain)s", {"chain": normalized}


# ── Whale moves (token_transfers) ─────────────────────────────────────────


def _rows_to_whale_moves(rows: list[tuple[Any, ...]], chain: str, limit: int) -> list[dict[str, Any]]:
    """Normalize token_transfers rows into whale-move channel items."""
    moves: list[dict[str, Any]] = []
    for row in rows[:limit]:
        chain_id = str(row[0] or chain)
        tx_hash = str(row[2] or "")
        from_addr = str(row[3] or "")
        to_addr = str(row[4] or "")
        token_addr = str(row[5] or "")
        value = float(row[6]) if row[6] else 0.0
        if not token_addr or value <= 0:
            continue
        from_label = _entity_label(from_addr)
        to_label = _entity_label(to_addr)
        symbol = _short_addr(token_addr)
        body_bits = [f"{_fmt_usd(value)} moved"]
        if from_addr:
            body_bits.append(f"from {_short_addr(from_addr)}{f' ({from_label})' if from_label else ''}")
        if to_addr:
            body_bits.append(f"to {_short_addr(to_addr)}{f' ({to_label})' if to_label else ''}")
        if tx_hash:
            body_bits.append(f"tx {_short_addr(tx_hash)}")
        moves.append(
            {
                "kind": "whale_move",
                "token_address": token_addr,
                "token_symbol": symbol,
                "title": f"Whale: {_fmt_usd(value)} {symbol} moved",
                "body": " · ".join(body_bits),
                "summary": " · ".join(body_bits),
                "chain": chain_id,
                "source": "token_transfers",
                "value_usd": value,
                "from_address": from_addr,
                "from_entity": from_label,
                "to_address": to_addr,
                "to_entity": to_label,
                "transaction_hash": tx_hash,
                "image_url": "",
            }
        )
    return moves


async def fetch_whale_moves(
    chain: str = "solana",
    limit: int = 5,
    min_value_usd: float = WHALE_MIN_VALUE_USD,
    lookback_hours: int = WHALE_LOOKBACK_HOURS,
) -> list[dict[str, Any]]:
    """Hourly top whale moves ≥ ``min_value_usd`` from own ``token_transfers``.

    Rows are entity-labeled via ``entity_registry``. Returns ``[]`` when
    ClickHouse is unavailable or no transfers clear the threshold.
    """
    since = (datetime.now(UTC) - timedelta(hours=lookback_hours)).strftime("%Y-%m-%d %H:%M:%S")
    chain_clause, params = _chain_filter(chain)
    params.update({"since": since, "min_val": min_value_usd, "limit": max(1, min(limit, 20))})
    rows = await _ch_query(
        f"""
        SELECT
            chain_id,
            block_timestamp,
            transaction_hash,
            from_address,
            to_address,
            token_address,
            value_decimal,
            transfer_type
        FROM wallet_memory.token_transfers
        WHERE block_timestamp >= %(since)s
          AND value_decimal > %(min_val)s
          {chain_clause}
        ORDER BY value_decimal DESC
        LIMIT %(limit)s
        """,  # noqa: S608 — chain_clause is code-literal; values bound via %(name)s
        params,
    )
    return _rows_to_whale_moves(rows, chain, limit)


# ── Early whale entries (deployer_events + first transfers) ──────────────


def _rows_to_early_entries(
    deploys: list[tuple[Any, ...]],
    per_token_rows: dict[str, list[tuple[Any, ...]]],
    chain: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Surface wallets that entered fresh tokens in the first blocks, with size."""
    entries: list[dict[str, Any]] = []
    for deploy in deploys[: limit * 2]:
        try:
            deployer = str(deploy[0] or "")
            token_addr = str(deploy[1] or "")
            ch_chain = str(deploy[2] or chain)
            deploy_block = int(deploy[3] or 0)
        except (TypeError, ValueError):
            continue
        if not token_addr:
            continue
        seen: set[str] = set()
        for row in per_token_rows.get(token_addr, []):
            if len(entries) >= limit:
                break
            try:
                block = int(row[0] or 0)
                wallet = str(row[2] or "")
                value = float(row[4]) if row[4] else 0.0
            except (TypeError, ValueError):
                continue
            if not wallet or wallet.lower() == deployer.lower() or value <= 0:
                continue
            if wallet in seen or _is_infrastructure(wallet):
                continue
            seen.add(wallet)
            label = _entity_label(wallet)
            delta = max(0, block - deploy_block) if deploy_block else 0
            symbol = _short_addr(token_addr)
            body = (
                f"{_fmt_usd(value)} bought within {delta} blocks of deploy"
                + (f" · {label}" if label else "")
                + f" · wallet {_short_addr(wallet)}"
            )
            entries.append(
                {
                    "kind": "early_entry",
                    "token_address": token_addr,
                    "token_symbol": symbol,
                    "title": f"🐣 Early whale: {_fmt_usd(value)} in {symbol}",
                    "body": body,
                    "summary": body,
                    "chain": ch_chain,
                    "source": "deployer_events",
                    "value_usd": value,
                    "wallet": wallet,
                    "entity": label,
                    "block_delta": delta,
                    "deployer": deployer,
                    "image_url": "",
                }
            )
    return entries[:limit]


async def fetch_early_whale_entries(
    chain: str = "solana",
    limit: int = 3,
    window_blocks: int = EARLY_ENTRY_BLOCK_WINDOW,
    lookback_hours: int = EARLY_ENTRY_LOOKBACK_HOURS,
) -> list[dict[str, Any]]:
    """Early whale entries: first-block buyers of fresh tokens, with size.

    Pulls recent token deploys from ``deployer_events``, then the earliest
    ``token_transfers`` inside ``window_blocks`` of each deploy block, and
    surfaces non-deployer, non-infrastructure receivers with their USD size.
    Bounded (a handful of tokens x a handful of wallets) and fail-open.
    """
    since = (datetime.now(UTC) - timedelta(hours=lookback_hours)).strftime("%Y-%m-%d %H:%M:%S")
    chain_clause, params = _chain_filter(chain)
    params.update({"since": since, "tokens": max(1, min(limit * 2, 10))})
    deploys = await _ch_query(
        f"""
        SELECT deployer, token_address, chain, block_number, block_timestamp, tx_hash
        FROM wallet_memory.deployer_events
        WHERE type IN ('token', 'pool')
          AND block_timestamp >= %(since)s
          {chain_clause}
        ORDER BY block_number DESC
        LIMIT %(tokens)s
        """,  # noqa: S608 — chain_clause is code-literal; values bound via %(name)s
        params,
    )

    per_token: dict[str, list[tuple[Any, ...]]] = {}
    for deploy in deploys[: limit * 2]:
        try:
            deployer = str(deploy[0] or "")
            token_addr = str(deploy[1] or "")
            ch_chain = str(deploy[2] or chain)
            deploy_block = int(deploy[3] or 0)
        except (TypeError, ValueError):
            continue
        if not token_addr:
            continue
        row_params: dict[str, Any] = {
            "token": token_addr,
            "max_block": deploy_block + window_blocks,
            "deployer": deployer,
            "wallets": 10,
        }
        token_clause, token_chain_params = _chain_filter(ch_chain)
        row_params.update(token_chain_params)
        rows = await _ch_query(
            f"""
            SELECT block_number, from_address, to_address, token_address,
                   value_decimal, transaction_hash
            FROM wallet_memory.token_transfers
            WHERE token_address = %(token)s
              AND block_number <= %(max_block)s
              AND to_address != %(deployer)s
              {token_clause}
            ORDER BY block_number ASC
            LIMIT %(wallets)s
            """,  # noqa: S608 — token_clause is code-literal; values bound via %(name)s
            row_params,
        )
        per_token[token_addr] = rows

    return _rows_to_early_entries(deploys, per_token, chain, limit)


# ── Smart money (live feed) ───────────────────────────────────────────────


async def fetch_smart_money_wallets(chain: str, limit: int) -> list[dict[str, Any]]:
    """Smart-money wallets from the live ``smart_money_feed`` (real source)."""
    try:
        result = await smart_money_feed(chain=chain, limit=max(1, min(limit, 20)))
    except Exception as exc:  # fail-open by contract
        logger.debug("channels:smart_money_feed_failed err=%s", str(exc)[:120])
        return []
    if not result or not isinstance(result, dict):
        return []
    wallets = result.get("smart_money_wallets")
    if not isinstance(wallets, list):
        return []
    normalized: list[dict[str, Any]] = []
    for w in wallets[:limit]:
        if not isinstance(w, dict) or not str(w.get("address") or "").strip():
            continue
        signal = str(w.get("signal") or "").strip()
        entity = str(w.get("entity") or "").strip()
        entity_type = str(w.get("entity_type") or "").strip()
        tags = [t for t in (signal, entity_type) if t]
        label = entity or signal or "Smart wallet"
        normalized.append(
            {
                "address": str(w.get("address") or "").strip(),
                "label": label,
                "tags": tags,
                "pnl_usd": w.get("estimated_usd") or w.get("pnl_usd"),
                "realized_pnl": w.get("realized_pnl"),
                "chain": chain,
                "image_url": "",
            }
        )
    return normalized


__all__ = [
    "fetch_early_whale_entries",
    "fetch_smart_money_wallets",
    "fetch_whale_moves",
]
