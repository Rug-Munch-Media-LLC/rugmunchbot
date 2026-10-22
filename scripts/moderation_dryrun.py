#!/usr/bin/env python3
"""Prove the moderation layer with synthetic updates THROUGH the dispatcher.

No polling, no network, no live chat: a RecordingBot records any Telegram
call the decision path would have made. With ``dry_run: true`` (the shipped
default) the expected number of recorded calls is ZERO — that is the proof
that nothing was deleted, muted, banned or posted.

    PYTHONPATH=src python scripts/moderation_dryrun.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from telegram import Bot  # noqa: E402 — after sys.path setup

CHAT_MAIN, CHAT_RAID, CHAT_MEDIA = -1001000000001, -1001000000002, -1001000000003
BENIGN_CA = "0x1111111111111111111111111111111111111111"

_config = {
    "version": 1,
    "chats": {
        str(CHAT_MAIN): {"enabled": True, "dry_run": True, "exempt_user_ids": [999]},
        str(CHAT_RAID): {"enabled": True, "dry_run": True, "chat_msgs": 6},
        str(CHAT_MEDIA): {"enabled": True, "dry_run": True, "media_burst": 2},
    },
}
_tmp = Path(tempfile.mkdtemp(prefix="modgate-"))
(_tmp / "moderation.json").write_text(json.dumps(_config))
os.environ["RUGMUNCH_MODERATION_CONFIG"] = str(_tmp / "moderation.json")
os.environ["RUGMUNCH_MODERATION_AUDIT"] = str(_tmp / "audit.jsonl")

CASES: list[tuple[str, list[dict]]] = []


def msg(chat: int, uid: int, text: str = "", name: str = "anon", mid: int = 1, **extra) -> dict:
    payload = {
        "message_id": mid,
        "date": 1_700_000_000,
        "chat": {"id": chat, "type": "supergroup", "title": f"chat{chat}"},
        "from": {"id": uid, "is_bot": False, "first_name": name, "username": f"u{uid}"},
    }
    if text:
        payload["text"] = text
    payload.update(extra)
    return {"update_id": mid, "message": payload}


CASES.append(
    ("1 promo spam", [msg(CHAT_MAIN, 101, "🚀 100x guaranteed, DM me for signals", mid=1)])
)
CASES.append(
    ("2 scam link", [msg(CHAT_MAIN, 102, "claim now https://claim-airdrop.xyz/verify", mid=2)])
)
CASES.append(
    ("3 fake airdrop", [msg(CHAT_MAIN, 103, "Airdrop is live — connect wallet to claim", mid=3)])
)
CASES.append(
    (
        "4 impersonation",
        [msg(CHAT_MAIN, 104, "official support here", name="RugMunch Support", mid=4)],
    )
)
CASES.append(("5 address shill", [msg(CHAT_MAIN, 105, f"new gem CA {BENIGN_CA} buy now", mid=5)]))
CASES.append(
    ("6 raid burst", [msg(CHAT_RAID, 600 + i, f"raid {i}", mid=100 + i) for i in range(6)])
)
CASES.append(("7 invite spam", [msg(CHAT_MAIN, 107, "join https://t.me/+AbCdEf123xyz", mid=7)]))
CASES.append(
    (
        "8 new-member media",
        [
            msg(
                CHAT_MEDIA,
                108,
                "",
                mid=8,
                photo=[{"file_id": "a", "file_unique_id": "a", "width": 1, "height": 1}],
            ),
            msg(
                CHAT_MEDIA,
                108,
                "",
                mid=9,
                photo=[{"file_id": "b", "file_unique_id": "b", "width": 1, "height": 1}],
            ),
        ],
    )
)
CASES.append(
    ("9 repeated message", [msg(CHAT_MAIN, 109, "same text here", mid=10 + i) for i in range(3)])
)
CASES.append(("10 unlisted link", [msg(CHAT_MAIN, 110, "check https://not-ours.xyz/foo", mid=13)]))
CASES.append(("11 clean control", [msg(CHAT_MAIN, 111, "gm frens", mid=14)]))
CASES.append(
    (
        "12 new member gate",
        [
            {
                "update_id": 15,
                "message": {
                    "message_id": 15,
                    "date": 1_700_000_000,
                    "chat": {"id": CHAT_MAIN, "type": "supergroup", "title": f"chat{CHAT_MAIN}"},
                    "from": {"id": 112, "is_bot": False, "first_name": "newbie"},
                    "new_chat_members": [{"id": 112, "is_bot": False, "first_name": "newbie"}],
                },
            }
        ],
    )
)
CASES.append(("13 exempt user", [msg(CHAT_MAIN, 999, "100x guaranteed DM me", mid=16)]))


class RecordingBot(Bot):
    """Stand-in for telegram.Bot: records instead of calling the API."""

    def __init__(self, calls: list[tuple[str, dict]]):
        super().__init__(token="123456:TEST")  # noqa: S106 — not a secret: invalid test token, never used online
        self._recorded = calls

    async def _rec(self, name: str, *args: object, **kwargs: object) -> object:
        self._recorded.append((name, kwargs if kwargs else {"args": args}))
        if name == "get_chat_member":
            from telegram.constants import ChatMemberStatus

            return SimpleNamespace(status=ChatMemberStatus.MEMBER)
        return True

    async def get_me(self, *args: object, **kwargs: object) -> object:
        from telegram import User

        return User(id=1, first_name="modgate", is_bot=True)

    async def delete_message(self, *a: object, **k: object) -> object:
        return await self._rec("delete_message", *a, **k)

    async def restrict_chat_member(self, *a: object, **k: object) -> object:
        return await self._rec("restrict_chat_member", *a, **k)

    async def ban_chat_member(self, *a: object, **k: object) -> object:
        return await self._rec("ban_chat_member", *a, **k)

    async def unban_chat_member(self, *a: object, **k: object) -> object:
        return await self._rec("unban_chat_member", *a, **k)

    async def send_message(self, *a: object, **k: object) -> object:
        return await self._rec("send_message", *a, **k)

    async def get_chat_member(self, *a: object, **k: object) -> object:
        return await self._rec("get_chat_member", *a, **k)


async def run() -> int:
    from telegram import Update
    from telegram.ext import Application

    from rugmunchbot.moderation import addresses, register_moderation
    from rugmunchbot.moderation.models import AddressVerdict

    calls: list[tuple[str, dict]] = []
    bot = RecordingBot(calls)

    async def fake_scan(address: str) -> AddressVerdict:
        return AddressVerdict(
            address=address,
            chain="ethereum",
            risk_level="critical",
            safety_score=5,
            verdict="avoid",
            reason="honeypot + mint authority",
        )

    addresses.scan_verdict = fake_scan  # type: ignore[assignment]

    app = Application.builder().bot(bot).build()
    register_moderation(app)
    await app.initialize()

    def audit_lines() -> list[str]:
        path = _tmp / "audit.jsonl"
        return path.read_text().strip().splitlines() if path.exists() else []

    rows = []
    for label, updates in CASES:
        before = len(audit_lines())
        for payload in updates:
            await app.process_update(Update.de_json(payload, bot))
        lines = audit_lines()
        last = json.loads(lines[-1]) if len(lines) > before else {}
        rows.append(
            (
                label,
                last.get("rule", "allow"),
                last.get("action", "allow"),
                last.get("dry_run", True),
                last.get("applied", False),
                last.get("reason", "no rule fired (allowed)"),
            )
        )

    print(f"{'#':<3}{'case':<22}{'rule':<18}{'action':<8}{'dry':<6}{'applied':<9}reason")
    for i, (label, rule, action, dry, applied, reason) in enumerate(rows, 1):
        print(f"{i:<3}{label:<22}{rule:<18}{action:<8}{dry!s:<6}{applied!s:<9}{reason[:52]}")

    live_calls = [c for c in calls if c[0] != "get_chat_member"]
    print(f"\naudit records: {len(audit_lines())}")
    print(f"telegram write calls: {len(live_calls)} (expected 0 in dry-run)")
    assert not live_calls, f"dry-run leaked live calls: {live_calls}"
    assert all(r[3] is True for r in rows), "a record was not dry-run"
    expected = [
        "promo",
        "scam_link",
        "scam_phrase",
        "impersonation",
        "address_risk",
        "chat_raid",
        "invite_link",
        "media_burst",
        "repeat_message",
        "unlisted_link",
        "allow",
        "new_member_gate",
        "allow",
    ]
    got = [r[1] for r in rows]
    assert got == expected, f"rule sequence mismatch:\n got={got}\n want={expected}"
    print("PASS — 13/13 decisions match the expected rule sequence, 0 live calls")
    await app.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
