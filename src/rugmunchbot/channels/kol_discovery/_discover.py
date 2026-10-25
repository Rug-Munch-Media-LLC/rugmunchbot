"""Continuous KOL auto-discovery for rugmunchbot.

This module continuously discovers new KOLs by monitoring shill campaigns
and automatically adds them to our monitoring list.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any

from rugmunchbot.channels.kol_discovery import (
    DISCOVERED_KOLS_PATH,
    _norm_handle,
    load_discovered_kols,
)
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.kol_discovery._discover")

# Configuration
DISCOVERY_INTERVAL_HOURS = int(os.getenv("RMI_KOL_CONTINUOUS_DISCOVERY_HOURS", "6"))
SHILL_KEYWORDS = {"shill", "promote", "dm for promo", "paid promotion", "sponsored"}
ANALYST_KEYWORDS = {"analyst", "trader", "investor", "research", "strategy"}
NEWS_KEYWORDS = {"news", "journalist", "reporter", "media", "press"}
SECURITY_KEYWORDS = {"security", "audit", "researcher", "research", "vulnerability", "exploit"}


async def get_user_info(username: str) -> dict[str, Any] | None:
    """Get user information from X — DISABLED: X ingestion owned by rugbrain.

    The bot no longer fetches X user profiles (ApiPass rail retired).
    X-derived intel is consumed through the RAG HTTP API. Fail-open ``None``.
    """
    logger.debug(
        "kol_discovery:get_user_info_skipped_x_ingest_owned_by_rugbrain username=%s", username
    )
    return None


async def discover_new_kols() -> int:
    """Discover new KOLs from shill campaigns — DISABLED: X owned by rugbrain.

    The bot no longer searches X for shilled tokens (ApiPass rail retired).
    X-derived intel is consumed through the RAG HTTP API. Fail-open ``0``.
    """
    logger.debug("kol_discovery:discover_new_kols_skipped_x_ingest_owned_by_rugbrain")
    return 0


async def run_continuous_discovery() -> None:
    """Run the continuous discovery loop."""
    logger.info(
        "kol_discovery:starting_continuous_discovery interval_hours=%d", DISCOVERY_INTERVAL_HOURS
    )

    while True:
        try:
            start_time = datetime.now(UTC)
            added = await discover_new_kols()
            elapsed = (datetime.now(UTC) - start_time).total_seconds()

            logger.info("kol_discovery:cycle_complete added=%d elapsed=%.2fs", added, elapsed)

            # Wait for the next interval
            await asyncio.sleep(DISCOVERY_INTERVAL_HOURS * 3600)

        except asyncio.CancelledError:
            logger.info("kol_discovery:discovery_loop_cancelled")
            break
        except Exception as exc:
            logger.error("kol_discovery:discovery_loop_error err=%s", str(exc)[:120])
            # Wait a bit before retrying to avoid tight error loops
            await asyncio.sleep(300)  # 5 minutes


def schedule_discovery() -> None:
    """Schedule the discovery to run every 6 hours.

    This function can be called to start the background discovery task.
    """
    # Create and run the discovery loop as a background task
    loop = asyncio.get_event_loop()
    if loop.is_running():
        task = loop.create_task(run_continuous_discovery())
        # Store reference to avoid RUF006
        return task
    else:
        loop.run_until_complete(run_continuous_discovery())


# For testing and direct execution
if __name__ == "__main__":
    import asyncio

    asyncio.run(run_continuous_discovery())
