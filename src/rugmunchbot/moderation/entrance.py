"""Join-time risk assessment: account-age proxy, profile and name heuristics.

The Bot API does not expose an account creation date, so "account age" is a
PROXY: Telegram user ids are issued monotonically, so an id above
new_account_id_min was almost certainly minted after that watermark. The proxy
is documented as such — it is one additive signal, never a verdict on its own,
and an operator can move the watermark or zero the weight per chat.

assess_join is pure (no I/O, no Telegram, no clock) so the same code path is
unit-tested and used live. It NEVER bans: the strongest outcome is
"quarantine" — a reversible hold that ends with a human release or a captcha
pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rugmunchbot.moderation.guard_settings import GuardSettings

# "rugmunch_support", "Airdrop Claim Bot", "official-helpdesk", ...
_SCAM_NAME = re.compile(
    r"(?:^|[^a-z])(airdrop|claim|reward|support|helpdesk|official|admin|moderator)(?:[^a-z]|$)",
    re.IGNORECASE,
)
# "user12345678", "john_4821" — machine-assigned looking handles.
_RANDOM_NAME = re.compile(r"^[a-z]{2,4}[_\-]?\d{4,}$", re.IGNORECASE)
_LETTER = re.compile(r"[A-Za-z]")


@dataclass(slots=True, frozen=True)
class JoinRisk:
    """One join's assessment: score, explainable reasons, reversible action."""

    user_id: int
    score: int
    reasons: tuple[str, ...]
    action: str  # "allow" | "verify" | "quarantine"

    @property
    def explained(self) -> str:
        return "; ".join(self.reasons) or "no signals"


def _name_signals(
    display_name: str, username: str, settings: GuardSettings
) -> list[tuple[int, str]]:
    name = (display_name or "").strip()
    signals: list[tuple[int, str]] = []
    if not name:
        signals.append((settings.score_empty_name, "no display name"))
    elif not _LETTER.search(name):
        signals.append((settings.score_empty_name, "display name has no letters"))
    if _RANDOM_NAME.match(name or username):
        signals.append((settings.score_bot_pattern, "machine-looking handle"))
    if not username and _SCAM_NAME.search(name):
        signals.append((settings.score_scam_name, "scam/authority keyword in name"))
    return signals


def assess_join(
    *,
    user_id: int,
    username: str = "",
    display_name: str = "",
    is_bot: bool = False,
    settings: GuardSettings | None = None,
) -> JoinRisk:
    """Score one joining account and return the reversible action to take."""
    cfg = settings or GuardSettings()
    if is_bot:
        action = "quarantine" if cfg.bot_quarantine else "allow"
        return JoinRisk(user_id, 100, ("bot account",), action)

    signals: list[tuple[int, str]] = []
    if user_id >= cfg.new_account_id_min:
        signals.append((cfg.score_new_account, "user id at/above new-account watermark"))
    if not username:
        signals.append((cfg.score_no_username, "no @username"))
    signals.extend(_name_signals(display_name, username, cfg))

    score = sum(points for points, _ in signals)
    reasons = tuple(reason for _, reason in signals)
    action = "quarantine" if score >= cfg.quarantine_score else "verify"
    return JoinRisk(user_id, score, reasons, action)


__all__ = ["JoinRisk", "assess_join"]
