"""Effective per-chat policy: safe defaults merged with the chat's JSON block."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Deliberately conservative: warn, never ban, never delete on first offence,
# and dry_run on. The merge is whitelist-only, so an unknown key is dropped
# and a missing key can never make a chat live.
DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "dry_run": True,
    "exempt_user_ids": [],
    "exempt_admins": False,  # no implicit admin bypass; opt in per chat
    "links_allow": [
        "rugmunch.io",
        "docs.rugmunch.io",
        "git.rugmunch.io",
        "t.me/rugmunch",
        "solscan.io",
        "etherscan.io",
        "dexscreener.com",
        "birdeye.so",
        "jup.ag",
        "jupiter.ag",
    ],
    "link_action": "warn",
    "scam_domains": [
        "claim-airdrop",
        "airdrop-claim",
        "claimreward",
        "claim-reward",
        "wallet-verify",
        "verifywallet",
        "tokenclaim",
        "connectwallet",
        "freemint-claim",
        "solreward",
        "bonus-claim",
    ],
    "scam_substrings": [
        "verify your wallet",
        "connect wallet to claim",
        "send sol to receive",
        "double your sol",
        "guaranteed airdrop",
    ],
    "promo_patterns": [
        r"(?i)\bdm\s+me\b.*\b(profit|signal|pump|call)",
        r"(?i)\b(100x|1000x|guaranteed\s+(profit|gains|x))\b",
        r"(?i)\b(presale|whitelist\s+spots?|private\s+sale)\b",
        r"(?i)\b(join|buy|ape)\s+(now|fast)\s+before\b",
        r"(?i)\bairdrop\s+(is\s+)?(live|claim|open)\b",
        r"(?i)\bfree\s+(sol|eth|usdt|usdc)\b",
        r"(?i)\b(first\s+\d+\s+(people|users))\b",
    ],
    "promo_action": "warn",
    "invite_action": "delete",
    "dup_count": 3,
    "dup_window_s": 120,
    "dup_action": "delete",
    "user_msgs": 5,
    "chat_msgs": 20,
    "velocity_window_s": 10,
    "user_velocity_action": "mute",
    "chat_velocity_action": "alert",
    "mute_minutes": 10,
    "ban_minutes": 0,
    "propagate_ban": False,
    "propagate_chats": [],
    "address_action": "warn",
    "address_avoid_action": "delete",
    "address_score": 40,
    "address_delete": False,
    "new_member_captcha": True,
    "captcha_type": "button",
    "captcha_timeout_s": 90,
    "captcha_mute_minutes": 10,
    "captcha_kick": False,
    "media_burst": 2,
    "nsfw_action": "delete",
    "nsfw_hashes": [],
    "ocr_scan": False,
    "impersonation_action": "warn",
    "impersonation_strict": False,
    "impersonation_roles": [
        "admin",
        "administrator",
        "owner",
        "dev",
        "developer",
        "moderator",
        "mod",
        "support",
        "official",
        "staff",
        "team",
    ],
    "welcome": "",
    "appeal_note": "Think this is wrong? DM the bot /appeal with this message id.",
}

_INT_TUPLES = ("exempt_user_ids", "propagate_chats")
_STR_TUPLES = (
    "links_allow",
    "scam_domains",
    "scam_substrings",
    "promo_patterns",
    "nsfw_hashes",
    "impersonation_roles",
)


def _clamp(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        return min(high, max(low, int(value)))
    except (TypeError, ValueError):
        return fallback


@dataclass(slots=True)
class ChatPolicy:
    """Effective policy for one chat (defaults merged with the chat block)."""

    chat_id: int
    enabled: bool = True
    dry_run: bool = True
    exempt_user_ids: tuple[int, ...] = ()
    exempt_admins: bool = False
    links_allow: tuple[str, ...] = ()
    link_action: str = "warn"
    scam_domains: tuple[str, ...] = ()
    scam_substrings: tuple[str, ...] = ()
    promo_patterns: tuple[str, ...] = ()
    promo_action: str = "warn"
    invite_action: str = "delete"
    dup_count: int = 3
    dup_window_s: int = 120
    dup_action: str = "delete"
    user_msgs: int = 5
    chat_msgs: int = 20
    velocity_window_s: int = 10
    user_velocity_action: str = "mute"
    chat_velocity_action: str = "alert"
    mute_minutes: int = 10
    ban_minutes: int = 0
    propagate_ban: bool = False
    propagate_chats: tuple[int, ...] = ()
    address_action: str = "warn"
    address_avoid_action: str = "delete"
    address_score: int = 40
    address_delete: bool = False
    new_member_captcha: bool = True
    captcha_type: str = "button"
    captcha_timeout_s: int = 90
    captcha_mute_minutes: int = 10
    captcha_kick: bool = False
    media_burst: int = 2
    nsfw_action: str = "delete"
    nsfw_hashes: tuple[str, ...] = ()
    ocr_scan: bool = False
    impersonation_action: str = "warn"
    impersonation_strict: bool = False
    impersonation_roles: tuple[str, ...] = ()
    welcome: str = ""
    appeal_note: str = ""

    @classmethod
    def from_dict(cls, chat_id: int, raw: dict[str, Any]) -> ChatPolicy:
        merged: dict[str, Any] = dict(DEFAULTS)
        merged.update({k: v for k, v in raw.items() if k in DEFAULTS})
        for key in _INT_TUPLES:
            merged[key] = tuple(int(v) for v in (merged.get(key) or ()))
        for key in _STR_TUPLES:
            merged[key] = tuple(str(v).lower() for v in (merged.get(key) or ()))
        merged["welcome"] = merged.get("welcome") or ""
        merged["captcha_timeout_s"] = _clamp(merged.get("captcha_timeout_s"), 30, 600, 90)
        merged["captcha_mute_minutes"] = _clamp(merged.get("captcha_mute_minutes"), 1, 1440, 10)
        merged["mute_minutes"] = _clamp(merged.get("mute_minutes"), 1, 1440, 10)
        merged["ban_minutes"] = _clamp(merged.get("ban_minutes"), 0, 10080, 0)
        if merged["captcha_type"] not in {"button", "math"}:
            merged["captcha_type"] = "button"
        return cls(chat_id=chat_id, **merged)

    def is_exempt(self, ctx: Any) -> bool:
        if ctx.user_id in self.exempt_user_ids:
            return True
        return bool(self.exempt_admins and ctx.is_admin)


__all__ = ["DEFAULTS", "ChatPolicy"]
