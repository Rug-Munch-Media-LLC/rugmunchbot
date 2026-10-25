"""ct_shill_watch — scheduled ticks (X ingestion retired, owned by rugbrain)."""

from __future__ import annotations

from typing import Any

from rugmunchbot.support.logging import get_logger

from . import _config
from ._wall import rescore_wall_of_shame

logger = get_logger("rmi_bot.channels.ct_shill_watch")


async def run_ct_shill_watch(ctx: Any = None) -> int:
    """One CT-timeline tick — DISABLED: X ingestion is owned by rugbrain (RAG).

    The bot no longer fetches X timelines (ApiPass rail retired). X-derived
    shill intel is consumed through the RAG HTTP API. Kept as a fail-open
    no-op so the scheduler registration and callers stay stable.
    """
    logger.debug("ct_shill:skipped_x_ingest_owned_by_rugbrain")
    return 0


async def discover_from_retweeters() -> int:
    """Retweeter-based KOL discovery — DISABLED: X ingestion owned by rugbrain.

    The bot no longer pulls X retweeter graphs (ApiPass rail retired).
    Fail-open no-op so the daily refresh caller stays stable.
    """
    logger.debug("ct_shill:retweeter_discovery_skipped_x_ingest_owned_by_rugbrain")
    return 0


async def run_kol_daily_refresh(ctx: Any = None) -> int:
    """Daily KOL improvement: rescore the wall + retweeter discovery.

    Returns the number of rescored entries (discovery count is logged).
    Fail-open.
    """
    if not _config.KOL_DAILY_REFRESH_ENABLED:
        return 0
    rescored = rescore_wall_of_shame()
    try:
        discovered = await discover_from_retweeters()
    except Exception as exc:
        logger.debug("ct_shill:retweeter_discovery_failed err=%s", str(exc)[:120])
        discovered = 0
    logger.info("ct_shill:daily_refresh rescored=%s discovered=%s", rescored, discovered)
    return rescored
