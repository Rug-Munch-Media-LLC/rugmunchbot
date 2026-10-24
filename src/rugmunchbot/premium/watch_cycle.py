"""One scan cycle for one watched token.

Order matters and is the whole point of this module:

1. take the snapshot from the live scan payload;
2. read the previous snapshot **before** overwriting it;
3. diff, and only if someone is watching, build alert rows;
4. write the rows through the canonical alert store;
5. store the new snapshot as the next baseline.

A token with no watchers still gets its snapshot refreshed (cheap, and it
keeps the baseline warm so the first watcher's first alert is meaningful), but
it produces no rows — an unwatched token must never fill the alert table.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.premium.bot_queue import enqueue_watch_alerts
from rugmunchbot.premium.snapshot import snapshot_from_scan
from rugmunchbot.premium.watch_events import diff_snapshots
from rugmunchbot.premium.watch_rows import rows_for_events
from rugmunchbot.premium.watch_store import (
    load_snapshot,
    save_snapshot,
    watcher_count,
    watchers_for,
)
from rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)


async def _persist_alerts(session: Any, rows: list[dict[str, Any]]) -> int:
    """Insert alert rows through the canonical backend store, fail-closed.

    PORT NOTE: the store is ``app.domains.alerts.postgres_store`` in rmi-backend
    (async SQLAlchemy, ``alerts`` table). The bot carries no alert table, so when
    that store is absent this returns 0 — and 0 inserted means nothing is queued
    for delivery, never a phantom page.
    """
    try:
        from app.domains.alerts.postgres_store import save_alerts
    except ImportError:  # bot runtime: no backend alert store installed
        logger.warning("premium_watch_cycle persist_unavailable rows=%d", len(rows))
        return 0
    return await save_alerts(session, rows)


async def run_watch_cycle(
    session: Any,
    redis: Any,
    token: str,
    chain: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Diff one scan against the stored baseline and persist any alerts.

    Args:
        session: async SQLAlchemy session for the canonical alerts store.
        redis: async Redis client holding watches and snapshots.
        token: mint/contract address being scanned.
        chain: chain the token lives on.
        payload: unified scanner result for this cycle.

    Returns:
        ``token``, ``chain``, ``watchers_n``, the ``events`` that fired, and
        how many alert rows were genuinely new (``inserted``).
    """
    snapshot = snapshot_from_scan(payload)
    previous = await load_snapshot(redis, token, chain)
    watchers_n = await watcher_count(redis, token, chain)

    events = diff_snapshots(previous, snapshot)
    rows = (
        rows_for_events(token, chain, events, watchers_n, snapshot) if watchers_n and events else []
    )
    inserted = await _persist_alerts(session, rows) if rows else 0
    watchers = await watchers_for(redis, token, chain)
    # Only a genuinely new alert is delivered: the store dedupes, so re-running
    # the same cycle must not page the same chat twice.
    queued = (
        await enqueue_watch_alerts(
            redis,
            token,
            chain,
            str(payload.get("token_symbol") or payload.get("symbol") or ""),
            events,
            snapshot,
            watchers,
        )
        if inserted
        else 0
    )

    await save_snapshot(redis, token, chain, snapshot)
    if events:
        logger.info(
            "premium_watch_cycle token=%s chain=%s watchers=%d events=%d inserted=%d",
            token[:12],
            chain,
            watchers_n,
            len(events),
            inserted,
        )
    return {
        "token": token,
        "chain": chain,
        "watchers_n": watchers_n,
        "baseline": previous is not None,
        "events": events,
        "alert_rows": len(rows),
        "inserted": inserted,
        "queued": queued,
        "watchers": watchers,
    }
