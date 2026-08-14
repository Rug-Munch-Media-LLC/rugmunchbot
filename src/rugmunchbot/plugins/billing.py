"""Billing provider seam.

Telegram Stars payments and premium tiers are PRIVATE (revenue logic of the
live deployment). OSS ships a no-op provider and a protocol so operators can
implement their own monetization without forking the bot.

Canonical private behavior: rmi-backend rugmunchbot handlers/payments.py +
config.py TIERS/TOP_UP_PACKS/BUY_STARS_PACKS/SCAN_PACKS (NOT ported).
"""

from __future__ import annotations

from typing import Any, Protocol


class BillingProvider(Protocol):
    """Entitlement check for a user. Implementations may check tiers, Stars
    balance, subscriptions, or anything else the operator wants."""

    async def check_entitlement(self, user_id: int, feature: str) -> bool: ...
    async def consume_quota(self, user_id: int, feature: str, amount: int = 1) -> bool: ...


class NoopBillingProvider:
    """Free-for-all: every feature is entitled, quotas never enforced.

    Safe default for self-hosting — the operator opts into enforcement by
    providing a real BillingProvider.
    """

    async def check_entitlement(self, user_id: int, feature: str) -> bool:
        return True

    async def consume_quota(self, user_id: int, feature: str, amount: int = 1) -> bool:
        return True


def get_billing_provider() -> Any:
    """Return the active provider (env-selected). Defaults to no-op."""
    return NoopBillingProvider()
