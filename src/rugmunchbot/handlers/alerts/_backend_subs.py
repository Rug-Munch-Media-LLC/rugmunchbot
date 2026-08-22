"""Alert engine backend webhook subscriptions (internal telegram bridge).

Fail-open by design: local price alerts keep working even when the backend
subscription cannot be created. Extracted verbatim from the former
single-module ``handlers/alerts.py``.
"""

from __future__ import annotations

import httpx

from rugmunchbot.config import BACKEND_URL, backend_headers
from rugmunchbot.handlers.alerts._constants import ALERT_EVENT_TYPES, MONITOR_EVENT_TYPES
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import get_shared_client

logger = get_logger("rmi_bot")


async def _subscribe_backend_alerts(
    user_id: int, token_address: str, event_types: list[str] | None = None
) -> bool:
    """Register the user's chat for real webhook events on a token.

    Fail-open: local price alerts keep working even if the backend
    subscription cannot be created (e.g. backend down).
    """
    try:
        client = get_shared_client(10)
        resp = await client.post(
            f"{BACKEND_URL}/api/v1/scanner/webhooks/telegram",
            json={
                "user_id": user_id,
                "event_types": event_types or ALERT_EVENT_TYPES,
                "token_addresses": [token_address],
            },
            headers=backend_headers(),
            timeout=10,
        )
        if resp.status_code in (200, 201):
            logger.info("alert_subscribed_backend user=%s token=%s", user_id, token_address[:10])
            return True
        logger.warning(
            "alert_backend_subscribe_failed user=%s status=%s body=%s",
            user_id,
            resp.status_code,
            resp.text[:200],
        )
        return False
    except (httpx.HTTPError, ConnectionError, TimeoutError, OSError) as e:
        logger.warning(f"alert_backend_subscribe_error user={user_id}: {e}")
        return False


async def _subscribe_backend_monitor(user_id: int, token_address: str) -> bool:
    """Subscribe a paid monitor to the live on-chain event stream.

    Reuses the same internal-telegram webhook bridge as price alerts, with
    the monitor event set (LP / whale / price). Fail-open like the alert
    path — the periodic ``check_monitors`` delta job still runs.
    """
    return await _subscribe_backend_alerts(user_id, token_address, event_types=MONITOR_EVENT_TYPES)


async def _unsubscribe_backend_monitor(user_id: int, token_address: str) -> bool:
    """Remove the monitor's internal webhook subscription for a token."""
    try:
        client = get_shared_client(10)
        resp = await client.request(
            "DELETE",
            f"{BACKEND_URL}/api/v1/scanner/webhooks/telegram",
            json={
                "user_id": user_id,
                "event_types": MONITOR_EVENT_TYPES,
                "token_addresses": [token_address],
            },
            headers=backend_headers(),
            timeout=10,
        )
        if resp.status_code in (200, 204):
            logger.info(
                "monitor_unsubscribed_backend user=%s token=%s", user_id, token_address[:10]
            )
            return True
        logger.warning(
            "monitor_backend_unsubscribe_failed user=%s status=%s body=%s",
            user_id,
            resp.status_code,
            resp.text[:200],
        )
        return False
    except (httpx.HTTPError, ConnectionError, TimeoutError, OSError) as e:
        logger.warning(f"monitor_backend_unsubscribe_error user={user_id}: {e}")
        return False
