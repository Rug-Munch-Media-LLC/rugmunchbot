"""Heimdall-class group moderation: policy, escalation, audit, enforcement.

Telegram-free and network-free: the caller injects a :class:`ModerationClient`.
Defaults are inert — ``dry_run=True`` and ``allow_auto_ban=False`` mean a
spammer is warned and audited, never removed, until an operator opts in.
Per-chat policies come from :func:`moderation_config.sync_policy`; no I/O here.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from rugmunchbot.community.moderation_access import AccessControl
from rugmunchbot.community.moderation_actions import act
from rugmunchbot.community.moderation_audit import AuditLog, JsonlAudit
from rugmunchbot.community.moderation_detect import detect_reasons
from rugmunchbot.community.moderation_models import (
    MessageEvent,
    ModerationClient,
    Policy,
    Verdict,
)
from rugmunchbot.community.moderation_window import DuplicateWindow

if TYPE_CHECKING:
    from collections.abc import Iterable


class ModerationEngine:
    """Per-chat policy, trust list, strikes, duplicate window and audit trail."""

    def __init__(
        self,
        policy: Policy | None = None,
        *,
        trusted: Iterable[int] = (),
        admins: Iterable[int] = (),
        clock: Any = time.time,
        audit_sink: JsonlAudit | None = None,
    ) -> None:
        self.policy = policy or Policy()
        self.access = AccessControl(trusted=trusted, admins=admins)
        self.audit = AuditLog(sink=audit_sink)
        self._policies: dict[int, Policy] = {}
        self._strikes: dict[tuple[int, int], int] = {}
        self._window = DuplicateWindow(clock)
        self._clock = clock

    def is_trusted(self, user_id: int) -> bool:
        return self.access.is_trusted(user_id)

    def is_admin(self, user_id: int) -> bool:
        return self.access.is_admin(user_id)

    def policy_for(self, chat_id: int | None = None) -> Policy:
        """The policy *chat_id* runs under — the default policy when none is set."""
        if chat_id is None:
            return self.policy
        return self._policies.get(int(chat_id), self.policy)

    def use_policy(self, chat_id: int, policy: Policy) -> None:
        """Install a loaded policy for one chat, leaving the default untouched."""
        self._policies[int(chat_id)] = policy

    def policy_summary(self, chat_id: int | None = None) -> dict[str, Any]:
        p = self.policy_for(chat_id)
        return {
            "dry_run": p.dry_run,
            "auto_ban": p.allow_auto_ban,
            "mute_after": p.strikes_mute,
            "ban_after": p.strikes_ban,
            "mute_seconds": p.mute_seconds,
            "max_mentions": p.max_mentions,
            "duplicate_threshold": p.duplicate_threshold,
            "new_account_days": p.new_account_days,
            **self.access.summary(),
        }

    def status(self, chat_id: int) -> dict[str, Any]:
        chat_id = int(chat_id)
        return {
            "chat_id": chat_id,
            "strikes": {uid: n for (cid, uid), n in self._strikes.items() if cid == chat_id},
            "actions": len(self.audit.actions(chat_id, limit=200)),
            "summary": self.policy_summary(chat_id),
        }

    def actions(self, chat_id: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
        return self.audit.actions(chat_id, limit)

    def inspect(self, ev: MessageEvent) -> list[str]:
        """Every detector hit for *ev* without consuming state."""
        policy = self.policy_for(ev.chat_id)
        return detect_reasons(ev, policy, frozenset(policy.trusted_hosts))

    def evaluate(self, ev: MessageEvent) -> Verdict:
        """Detect, count a strike, escalate, and append one audit row."""
        policy = self.policy_for(ev.chat_id)
        reasons = self.inspect(ev)
        if self._window.hit(ev, policy):
            reasons.append("duplicate_spam")
        if not reasons or ev.is_admin or self.is_trusted(ev.user_id):
            if reasons:
                self._record(ev, Verdict(reasons=tuple(reasons), dry_run=policy.dry_run))
            return Verdict(reasons=tuple(reasons), dry_run=policy.dry_run)

        key = (int(ev.chat_id), int(ev.user_id))
        self._strikes[key] = self._strikes.get(key, 0) + 1
        strikes = self._strikes[key]
        level, capped = "warn", False
        if strikes >= policy.strikes_mute:
            level = "mute"
        if strikes >= policy.strikes_ban:
            if policy.allow_auto_ban:
                level = "ban"
            else:
                level, capped = "mute", True
        verdict = Verdict(
            level=level,
            reasons=tuple(reasons),
            strikes=strikes,
            dry_run=policy.dry_run,
            capped=capped,
        )
        self._record(ev, verdict)
        return verdict

    async def enforce(self, ev: MessageEvent, verdict: Verdict, client: ModerationClient) -> str:
        """Perform the verdict action unless this chat is in dry-run."""
        if verdict.level == "ok":
            return "none"
        policy = self.policy_for(ev.chat_id)
        if verdict.dry_run or policy.dry_run:
            return "dry_run"
        return await act(
            policy, verdict.level, ev.chat_id, ev.user_id, client, ev.message_id, now=self._clock()
        )

    async def enforce_manual(
        self,
        action: str,
        chat_id: int,
        user_id: int,
        client: ModerationClient | None = None,
        *,
        reason: str = "",
        message_id: int = 0,
    ) -> str:
        """Apply an admin command (warn, mute, ban, trust, untrust, pardon)."""
        chat_id, user_id = int(chat_id), int(user_id)
        policy = self.policy_for(chat_id)
        if action == "trust":
            self.access.trust(user_id)
        elif action == "untrust":
            self.access.untrust(user_id)
        elif action == "pardon":
            self._strikes.pop((chat_id, user_id), None)
        elif action not in ("warn", "mute", "ban"):
            raise ValueError(f"unknown moderation action: {action}")
        outcome = "recorded"
        if action in ("warn", "mute", "ban"):
            outcome = "dry_run"
            if not policy.dry_run and client is not None:
                outcome = await act(
                    policy, action, chat_id, user_id, client, message_id, now=self._clock()
                )
        self.audit.record(
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "message_id": int(message_id),
                "level": action,
                "reasons": [reason] if reason else ["manual"],
                "strikes": self._strikes.get((chat_id, user_id), 0),
                "dry_run": policy.dry_run,
                "capped": False,
                "at": self._clock(),
            }
        )
        return outcome

    def _record(self, ev: MessageEvent, verdict: Verdict) -> None:
        self.audit.record(
            {
                "chat_id": int(ev.chat_id),
                "user_id": int(ev.user_id),
                "message_id": int(ev.message_id),
                "level": verdict.level,
                "reasons": list(verdict.reasons),
                "strikes": verdict.strikes,
                "dry_run": verdict.dry_run,
                "capped": verdict.capped,
                "at": self._clock(),
            }
        )
