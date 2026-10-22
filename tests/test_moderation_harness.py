"""The dry harness must prove 15+ scenarios with zero Telegram writes."""

from __future__ import annotations

import pytest

from rugmunchbot.moderation import harness, harness_cases


def test_harness_covers_at_least_fifteen_cases():
    assert len(harness_cases.CASES) >= 15
    names = {name for name, _, _ in harness_cases.CASES}
    for required in {
        "join",
        "captcha_pass",
        "captcha_fail",
        "scam_link",
        "promo",
        "repeat_message",
        "chat_raid",
        "address_avoid",
        "invite_link",
        "media_burst",
        "impersonation",
        "clean_message",
    }:
        assert required in names


@pytest.mark.asyncio
async def test_every_scenario_matches_and_writes_nothing(capsys):
    assert await harness._run() == 0
    out = capsys.readouterr().out
    assert "telegram_api_calls=0" in out
    assert "FAIL" not in out
