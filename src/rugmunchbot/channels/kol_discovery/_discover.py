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


def classify_kol(username: str, bio: str) -> str:
    """Classify a KOL based on their bio keywords.

    Args:
        username: The Twitter/X handle (without @)
        bio: The user's bio description

    Returns:
        One of: shiller, analyst, news, security, or unknown
    """
    bio_lower = bio.lower()

    # Check for shiller keywords
    if any(keyword in bio_lower for keyword in SHILL_KEYWORDS):
        return "shiller"

    # Check for analyst keywords
    if any(keyword in bio_lower for keyword in ANALYST_KEYWORDS):
        return "analyst"

    # Check for news keywords
    if any(keyword in bio_lower for keyword in NEWS_KEYWORDS):
        return "news"

    # Check for security keywords
    if any(keyword in bio_lower for keyword in SECURITY_KEYWORDS):
        return "security"

    return "unknown"


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


async def _update_kol_metadata(handle: str, metadata: dict[str, Any]) -> bool:
    """Update additional metadata for a discovered KOL.

    Args:
        handle: The KOL's handle (without @)
        metadata: Dictionary with follower_count, bio, category, first_seen, evidence_count

    Returns:
        True if successful
    """
    try:
        # Load existing discovered KOLs
        records = load_discovered_kols()
        handle_norm = _norm_handle(handle)

        # Find the record for this handle
        record = next((r for r in records if _norm_handle(r.get("handle")) == handle_norm), None)
        if record is None:
            logger.warning("kol_discovery:kol_not_found_for_metadata_update handle=%s", handle)
            return False

        # Update the record with our metadata
        record.update(
            {
                "follower_count": metadata.get("follower_count", 0),
                "bio": metadata.get("bio", ""),
                "category": metadata.get("category", "unknown"),
                "first_seen": metadata.get("first_seen"),
                "evidence_count": metadata.get("evidence_count", 1),
            }
        )

        # Save back to file
        os.makedirs(os.path.dirname(DISCOVERED_KOLS_PATH), exist_ok=True)
        payload = {
            "updated": datetime.now(UTC).isoformat(),
            "count": len(records),
            "discovered": records,
        }

        # Use asyncio.to_thread to avoid blocking the event loop
        def _write_file():
            tmp = DISCOVERED_KOLS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, DISCOVERED_KOLS_PATH)

        await asyncio.to_thread(_write_file)

        return True

    except Exception as exc:
        logger.error(
            "kol_discovery:update_metadata_failed handle=%s err=%s", handle, str(exc)[:120]
        )
        return False


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
