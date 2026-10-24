"""Premium endpoints — per-token watches, cycle, export and webhook contract.

PORT NOTE (rugmunchbot copy): this is the rmi-backend *API layer*, restored
from ``app/domains/premium/router.py`` for provenance. The bot never serves
HTTP — it calls these routes through ``handlers/premium_watch/_api.py`` — and
the four ``app.core.*`` seams below (DB/redis deps, internal key, rate limiter)
exist only in the backend, so this module is deliberately NOT exported from
``rugmunchbot.premium.__init__`` and is not importable bot-side.

Routes (paths are mounted by ``app.routers.v1`` under ``/api/v1``):

* ``POST   /watch``             subscribe a chat to a token
* ``DELETE /watch``             unsubscribe
* ``GET    /watch``             what a chat (or the fleet) is watching
* ``POST   /watch/cycle``       diff watched tokens against their baseline
* ``GET    /scan/{token}.json`` canonical export — the webhook body
* ``GET    /webhook/contract``  the transport/auth half of the same contract

The cycle endpoint is the seam the scan scheduler calls; it never scans a
token nobody watches, and it is internal-key gated because a cycle writes
alert rows.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any

from app.core.dependencies import get_db, get_redis
from app.core.internal_key import get_internal_key_or_none
from app.core.rate_limiter import get_rate_limit_dependency
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from rugmunchbot.premium.export import export_payload, webhook_contract
from rugmunchbot.premium.service import derive, scan_payload
from rugmunchbot.premium.watch_cycle import run_watch_cycle
from rugmunchbot.premium.watch_store import add_watch, list_watches, remove_watch, watcher_count
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)
router = APIRouter(tags=["premium"])

#: One export per 2s per IP, bursting to 5 — a file download, not a feed.
export_rate_limit = Depends(get_rate_limit_dependency("premium_export", rate=0.5, burst=5))
MAX_CYCLE_TOKENS = 25


class WatchBody(BaseModel):
    """A chat subscribing to one token."""

    token: str = Field(min_length=8, max_length=64)
    chat_id: str = Field(min_length=1, max_length=64)
    chain: str = Field(default="solana", max_length=32)


class CycleBody(BaseModel):
    """Which watched token to diff; omitted means every watched token."""

    token: str | None = Field(default=None, max_length=64)
    chain: str = Field(default="solana", max_length=32)


def _store(redis: Any) -> Any:
    if redis is None:
        raise HTTPException(status_code=503, detail="watch store unavailable (redis down)")
    return redis


def _guard_internal(request: Request) -> None:
    """Reject a caller that is not us — a cycle writes alert rows."""
    expected = get_internal_key_or_none()
    supplied = request.headers.get("X-RMI-Key", "")
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="internal key required")


@router.post("/watch", summary="Watch a token and alert this chat")
async def create_watch(body: WatchBody, redis: Any = Depends(get_redis)) -> dict[str, Any]:
    """Subscribe ``chat_id`` to a token; idempotent, so re-posting is a no-op."""
    count = await add_watch(_store(redis), body.token, body.chain, body.chat_id)
    return {
        "token": body.token,
        "chain": body.chain,
        "chat_id": body.chat_id,
        "watching": True,
        "watchers_n": count,
    }


@router.delete("/watch", summary="Stop watching a token")
async def delete_watch(
    token: str = Query(min_length=8, max_length=64),
    chat_id: str = Query(min_length=1, max_length=64),
    chain: str = Query(default="solana", max_length=32),
    redis: Any = Depends(get_redis),
) -> dict[str, Any]:
    """Remove one subscription and report whether it existed."""
    store = _store(redis)
    removed = await remove_watch(store, token, chain, chat_id)
    return {
        "token": token,
        "chain": chain,
        "chat_id": chat_id,
        "removed": removed,
        "watchers_n": await watcher_count(store, token, chain),
    }


@router.get("/watch", summary="List watches")
async def get_watches(
    chat_id: str = Query(default="", max_length=64),
    redis: Any = Depends(get_redis),
) -> dict[str, Any]:
    """Watches for one chat, or every watched token with its watcher count."""
    rows = await list_watches(_store(redis), chat_id)
    return {"watches": rows, "count": len(rows), "chat_id": chat_id or None}


@router.post("/watch/cycle", summary="Diff watched tokens against their baseline")
async def watch_cycle(
    request: Request,
    body: CycleBody,
    session: AsyncSession = Depends(get_db),
    redis: Any = Depends(get_redis),
) -> dict[str, Any]:
    """Scan and diff watched tokens, emitting alert rows on any change."""
    _guard_internal(request)
    store = _store(redis)
    if body.token:
        targets = [{"token": body.token, "chain": body.chain}]
    else:
        targets = (await list_watches(store))[:MAX_CYCLE_TOKENS]
    cycles: list[dict[str, Any]] = []
    for target in targets:
        try:
            scan = await scan_payload(target["token"], target["chain"])
            cycles.append(
                await run_watch_cycle(session, store, target["token"], target["chain"], scan)
            )
        except Exception as exc:  # one bad token must not abort the cycle
            logger.warning(
                "watch_cycle_failed token=%s err=%s", str(target["token"])[:12], str(exc)[:160]
            )
            cycles.append(
                {"token": target["token"], "chain": target["chain"], "error": str(exc)[:160]}
            )
    return {"cycles": cycles, "count": len(cycles)}


@router.get(
    "/scan/{token}.json",
    summary="Canonical scan export (webhook body)",
    dependencies=[export_rate_limit],
)
async def export_scan_json(
    token: str,
    chain: str = Query(default="solana", max_length=32),
    redis: Any = Depends(get_redis),
) -> dict[str, Any]:
    """Export one token as the versioned payload every webhook also carries."""
    scan = await scan_payload(token, chain)
    snapshot, graph, rollup = derive(scan)
    watchers_n = await watcher_count(redis, token, chain) if redis is not None else 0
    return export_payload(
        scan, bundle_graph=graph, rollup=rollup, snapshot=snapshot, watchers_n=watchers_n
    )


@router.get("/webhook/contract", summary="Webhook payload + transport contract")
async def webhook_contract_route() -> dict[str, Any]:
    """Describe the schema and signing rules a webhook subscriber implements."""
    return webhook_contract()
