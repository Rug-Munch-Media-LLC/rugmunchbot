# VENDORED 2026-08-24 from rmi-backend/app/domains/alerts/publisher.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Platform-alert routing — pure decision layer.

Maps normalized ``PlatformAlert`` objects to downstream surfaces based on a
severity threshold + category allowlist per channel. Produces a routing
PLAN only — this module never performs a network send. The transport is an
injectable ``send: Callable`` that the orchestrator supplies and executes;
the default is a no-op stub.

Default channels
-----------------
    site            — all alerts
    alerts_channel  — @rmialerts: high+
    rmialerts       — critical + high
    rmialpha        — highest-signal early data: launch, syndicate, whale
                      (high+). Alpha = the earliest, most actionable alerts
                      before they reach the broader news surfaces.
    rmicryptonews   — news-worthy categories (scanner, launch, bulletin,
                      whale, cex_flow)
    main_channel    — critical only
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from rugmunchbot._vendor.alerts_models import PlatformAlert

SEVERITY_RANK: dict[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1}

# Categories considered news-worthy for the rmicryptonews surface.
NEWS_CATEGORIES = frozenset({"scanner", "launch", "bulletin", "whale", "cex_flow"})

# Highest-signal early-data categories for the rmialpha surface (alpha).
# Alpha = the earliest, most actionable on-chain signals: token launches,
# syndicate activity, and whale moves.
ALPHA_CATEGORIES = frozenset({"launch", "syndicate", "whale"})


@dataclass(frozen=True)
class ChannelPolicy:
    """Routing policy for one surface: min severity + category allowlist."""

    min_severity: str = "low"
    categories: frozenset[str] | None = None  # None = all categories

    def matches(self, alert: PlatformAlert) -> bool:
        """True if an alert belongs on a channel with this policy."""
        if SEVERITY_RANK.get(alert.severity, 0) < SEVERITY_RANK.get(self.min_severity, 0):
            return False
        return self.categories is None or alert.category in self.categories


DEFAULT_CHANNELS: dict[str, ChannelPolicy] = {
    "site": ChannelPolicy(min_severity="low"),
    "alerts_channel": ChannelPolicy(min_severity="high"),
    "rmialerts": ChannelPolicy(min_severity="high", categories=frozenset({"scanner", "bulletin", "cex_flow", "security_incident", "rug_confirmed", "exploit", "hack"})),
    "rmialpha": ChannelPolicy(min_severity="high", categories=ALPHA_CATEGORIES),
    "rmicryptonews": ChannelPolicy(min_severity="low", categories=NEWS_CATEGORIES),
    "main_channel": ChannelPolicy(min_severity="critical"),
}


def _noop_send(payload: dict[str, Any]) -> None:
    """Transport stub — real sends are injected by the caller."""


def route_alerts(
    alerts: list[PlatformAlert],
    *,
    channel_filter: dict[str, ChannelPolicy] | None = None,
    send: Callable[[dict[str, Any]], Any] | None = None,
) -> list[dict[str, Any]]:
    """Return the routing plan: which alerts go to which surface.

    Pure — ``send`` is embedded in the plan but never invoked here; the
    orchestrator hands the plan to a transport layer for execution.
    """
    policies = channel_filter if channel_filter is not None else DEFAULT_CHANNELS
    transport = send if send is not None else _noop_send
    plan: list[dict[str, Any]] = []
    for channel, policy in policies.items():
        routed = [alert.to_dict() for alert in alerts if policy.matches(alert)]
        plan.append(
            {
                "channel": channel,
                "min_severity": policy.min_severity,
                "categories": sorted(policy.categories) if policy.categories is not None else None,
                "count": len(routed),
                "alerts": routed,
                "send": transport,
            }
        )
    return plan
