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
    add_discovered_kol,
    extract_tweet_tokens,
    load_discovered_kols,
    load_tracked_handles,
)
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools import apipass_x

logger = get_logger("rmi_bot.channels.kol_discovery._discover")

# Configuration
DISCOVERY_INTERVAL_HOURS = int(os.getenv("RMI_KOL_CONTINUOUS_DISCOVERY_HOURS", "6"))
MIN_FOLLOWERS_FOR_CRYPTO = int(os.getenv("RMI_KOL_MIN_FOLLOWERS_CRYPTO", "10000"))
MIN_SHILL_CAMPAIGNS_THRESHOLD = int(os.getenv("RMI_KOL_MIN_SHILL_CAMPAIGNS", "3"))
SHILL_KEYWORDS = {"shill", "promote", "dm for promo", "paid promotion", "sponsored"}
ANALYST_KEYWORDS = {"analyst", "trader", "investor", "research", "strategy"}
NEWS_KEYWORDS = {"news", "journalist", "reporter", "media", "press"}
SECURITY_KEYWORDS = {"security", "audit", "researcher", "research", "vulnerability", "exploit"}

# Track shill campaign appearances per handle
_shill_campaign_counts: dict[str, int] = {}
_last_discovery_run: datetime | None = None


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
    """Get user information from X API.

    Args:
        username: The Twitter/X handle (without @)

    Returns:
        Dictionary with user info or None if failed
    """
    try:
        # Use the existing apipass_x client to get user info
        # This assumes apipass_x has a method for user/info endpoint
        if hasattr(apipass_x, 'get_user_info'):
            return await apipass_x.get_user_info(username)
        else:
            # Fallback: try to use search_tweets with the username to get basic info
            # This is less ideal but provides a fallback
            tweets = await apipass_x.search_tweets(f"from:{username}", limit=1)
            if tweets and len(tweets) > 0:
                # Extract basic user info from the tweet
                tweet = tweets[0]
                return {
                    "username": tweet.get("handle", username),
                    "follower_count": tweet.get("follower_count", 0),
                    "bio": tweet.get("user_description", ""),
                    "verified": tweet.get("verified", False)
                }
    except Exception as exc:
        logger.debug("kol_discovery:get_user_info_failed username=%s err=%s",
                    username, str(exc)[:120])

    return None


async def discover_new_kols() -> int:
    """Discover new KOLs from shill campaigns and add them to monitoring.

    Returns:
        Number of new KOLs discovered and added
    """
    global _shill_campaign_counts, _last_discovery_run

    try:
        # Get current tracked handles to avoid duplicates
        tracked_handles = load_tracked_handles()
        discovered_kols = {_norm_handle(k.get("handle")) for k in load_discovered_kols()}
        already_tracked = tracked_handles.union(discovered_kols)

        logger.info("kol_discovery:starting_discovery_tracked=%d", len(already_tracked))

        # Scan for recent shill campaigns (we'll reuse logic from existing kol_discovery)
        # Get seed tokens from recent shill evidence
        from rugmunchbot.db._base import get_db

        conn = get_db()
        try:
            # Get tokens that have appeared in shill campaigns recently
            rows = conn.execute(
                """SELECT token, COUNT(*) as campaign_count
                   FROM shill_evidence
                   WHERE datetime(created_at) > datetime('now', '-24 hours')
                   GROUP BY token
                   HAVING campaign_count >= ?
                   ORDER BY campaign_count DESC
                   LIMIT 20""",
                (MIN_SHILL_CAMPAIGNS_THRESHOLD,),
            ).fetchall()
        finally:
            conn.close()

        # Extract tokens that meet our threshold
        suspicious_tokens = [str(row[0]).strip() for row in rows if str(row[0]).strip()]

        if not suspicious_tokens:
            logger.info("kol_discovery:no_suspicious_tokens_found")
            return 0

        logger.info("kol_discovery:found_suspicious_tokens=%d", len(suspicious_tokens))

        # Search for tweets about these tokens
        new_kols_added = 0

        for token in suspicious_tokens:
            if new_kols_added >= 10:  # Limit discoveries per run
                break

            try:
                # Search for recent tweets about this token
                tweets = await apipass_x.search_tweets(token, limit=50)

                for tweet in tweets or []:
                    if not isinstance(tweet, dict):
                        continue

                    handle = _norm_handle(tweet.get("handle"))
                    if not handle or handle in already_tracked:
                        continue

                    # Check engagement
                    engagement = int(tweet.get("like_count") or 0) + int(tweet.get("retweet_count") or 0)
                    if engagement < 10:  # Minimum engagement threshold
                        continue

                    # Get user info to check follower count and bio
                    user_info = await get_user_info(handle)
                    if not user_info:
                        continue

                    follower_count = int(user_info.get("follower_count") or 0)
                    bio = str(user_info.get("bio") or "")

                    # Check if meets criteria: >10K followers and tweets about crypto tokens
                    text = str(tweet.get("text") or "")
                    token_signals = extract_tweet_tokens(text)
                    has_crypto_token = len([t for t in token_signals if t.upper() not in {"$BTC", "$ETH", "$SOL", "$USDT", "$USDC", "$BNB"}]) > 0

                    if follower_count >= MIN_FOLLOWERS_FOR_CRYPTO and has_crypto_token:
                        # Classify the KOL
                        category = classify_kol(handle, bio)

                        # Prepare evidence
                        evidence = {
                            "tweet_id": str(tweet.get("tweet_id", "")),
                            "url": str(tweet.get("url", "")),
                            "text": text[:200],
                            "likes": int(tweet.get("like_count") or 0),
                            "retweets": int(tweet.get("retweet_count") or 0),
                            "engagement": engagement,
                            "token_mentioned": token,
                            "captured_at": datetime.now(UTC).isoformat(),
                        }

                        # Add to discovered KOLs
                        if add_discovered_kol(
                            handle,
                            token.upper(),
                            evidence
                        ):
                            # Also track our custom fields in a separate file or extend the record
                            await _update_kol_metadata(handle, {
                                "follower_count": follower_count,
                                "bio": bio,
                                "category": category,
                                "first_seen": datetime.now(UTC).isoformat(),
                                "evidence_count": 1
                            })

                            new_kols_added += 1
                            logger.info(
                                "kol_discovery:new_kol_added handle=%s followers=%d category=%s token=%s",
                                handle, follower_count, category, token
                            )

                            # Add to already_tracked to avoid duplicates in this run
                            already_tracked.add(handle)

            except Exception as exc:
                logger.debug("kol_discovery:token_scan_failed token=%s err=%s",
                           token, str(exc)[:120])
                continue

        _last_discovery_run = datetime.now(UTC)
        logger.info("kol_discovery:discovery_complete added=%d", new_kols_added)
        return new_kols_added

    except Exception as exc:
        logger.error("kol_discovery:discovery_failed err=%s", str(exc)[:120])
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
        record.update({
            "follower_count": metadata.get("follower_count", 0),
            "bio": metadata.get("bio", ""),
            "category": metadata.get("category", "unknown"),
            "first_seen": metadata.get("first_seen"),
            "evidence_count": metadata.get("evidence_count", 1)
        })

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
        logger.error("kol_discovery:update_metadata_failed handle=%s err=%s",
                    handle, str(exc)[:120])
        return False


async def run_continuous_discovery() -> None:
    """Run the continuous discovery loop."""
    logger.info("kol_discovery:starting_continuous_discovery interval_hours=%d",
                DISCOVERY_INTERVAL_HOURS)

    while True:
        try:
            start_time = datetime.now(UTC)
            added = await discover_new_kols()
            elapsed = (datetime.now(UTC) - start_time).total_seconds()

            logger.info("kol_discovery:cycle_complete added=%d elapsed=%.2fs",
                       added, elapsed)

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
