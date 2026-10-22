"""Dry-run harness: drive 17 synthetic updates through the real guard.

    python -m rugmunchbot.moderation.harness

Writes a throwaway config + audit file, runs every scenario with ``dry_run``
on, prints ``case / rule / action / reason`` and fails if any scenario fired
the wrong rule or if the fake bot was called even once (zero Telegram writes).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from rugmunchbot.moderation import audit, config, harness_cases, state
from rugmunchbot.moderation.harness_fakes import (
    CHAT_ID,
    MATH_CHAT_ID,
    FakeBot,
    FakeChat,
    FakeQueue,
)

BASE = {
    "enabled": True,
    "dry_run": True,
    "new_member_captcha": True,
    "captcha_timeout_s": 90,
    "captcha_type": "button",
}


def _write_config(path: Path) -> None:
    chats = {
        str(CHAT_ID): dict(BASE),
        str(MATH_CHAT_ID): {**BASE, "captcha_type": "math", "captcha_kick": True},
    }
    path.write_text(json.dumps({"version": 2, "chats": chats}), encoding="utf-8")


def _header() -> None:
    print(f"{'#':>2}  {'case':16} {'rule':17} {'action':7} reason")
    print("-" * 100)


async def _run() -> int:
    old_cfg, old_log = config.CONFIG_PATH, audit.AUDIT_PATH
    saved = (
        os.environ.get("RUGMUNCH_MODERATION_CONFIG"),
        os.environ.get("RUGMUNCH_MODERATION_AUDIT"),
    )
    with tempfile.TemporaryDirectory(prefix="mod-harness-") as tmp:
        cfg, log = Path(tmp) / "moderation.json", Path(tmp) / "actions.jsonl"
        _write_config(cfg)
        os.environ["RUGMUNCH_MODERATION_CONFIG"] = str(cfg)
        os.environ["RUGMUNCH_MODERATION_AUDIT"] = str(log)
        config.use_config_path(cfg)
        audit.use_audit_path(log)

        rig = SimpleNamespace(bot=FakeBot(), queue=FakeQueue(), chat=FakeChat(CHAT_ID))
        _header()
        failures: list[str] = []
        for index, (name, case, expected) in enumerate(harness_cases.CASES, start=1):
            state.reset()
            rule, action, reason = await case(rig)
            mark = "ok" if rule == expected else f"FAIL want {expected}"
            if rule != expected:
                failures.append(f"{name}: got {rule}, want {expected}")
            print(f"{index:>2}  {name:16} {rule:17} {action:7} {reason[:44]}  {mark}")

        rows = sum(1 for _ in log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0
        print("-" * 100)
        print(
            f"cases={len(harness_cases.CASES)} audit_rows={rows} telegram_api_calls={len(rig.bot.calls)}"
        )
        if rig.bot.calls:
            failures.append(f"telegram writes attempted: {sorted(set(rig.bot.calls))}")
        if len(harness_cases.CASES) < 15:
            failures.append("fewer than 15 scenarios")
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        config.use_config_path(old_cfg)
        audit.use_audit_path(old_log)
        for key, value in (
            ("RUGMUNCH_MODERATION_CONFIG", saved[0]),
            ("RUGMUNCH_MODERATION_AUDIT", saved[1]),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return 1 if failures else 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
