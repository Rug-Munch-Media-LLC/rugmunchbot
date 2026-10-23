"""Referral value object — one resolved referral surface."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Referral:
    """A resolved referral surface.

    ``url`` is ``None`` when the platform has no configured referral link.
    ``reason`` then says why, so callers render ``null`` + reason instead of
    inventing a link.
    """

    platform: str
    tier: str
    url: str | None = None
    account: str | None = None
    reason: str | None = None

    @property
    def available(self) -> bool:
        """True only when a real link exists and nothing blocked it."""
        return bool(self.url) and self.reason is None

    def as_dict(self) -> dict[str, str | None]:
        """JSON-safe shape — ``url`` stays ``None`` when unconfigured."""
        return {
            "platform": self.platform,
            "tier": self.tier,
            "url": self.url,
            "account": self.account if self.available else None,
            "reason": self.reason,
        }
