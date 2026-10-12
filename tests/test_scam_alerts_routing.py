"""Scam alerts route by severity: alerts page always, MAIN only for the big ones."""

from __future__ import annotations

import pytest

from rugmunchbot.channels.scam_alerts import _publisher as pub


def _alert(risk: int, ident: str = "x") -> dict:
    return {"id": ident, "text": "100x gem guaranteed returns DM me",
            "metadata": {"risk": risk, "channel": "alphacallsofficial"}}


def test_low_and_mid_risk_go_to_the_alerts_page_only():
    assert pub.destinations_for(_alert(3)) == ("alerts",)
    assert pub.destinations_for(_alert(4)) == ("alerts",)


def test_big_scam_also_reaches_main():
    assert pub.destinations_for(_alert(5)) == ("alerts", "main")
    assert pub.destinations_for(_alert(9)) == ("alerts", "main")


def test_main_threshold_is_configurable(monkeypatch):
    monkeypatch.setattr(pub, "MAIN_RISK", 7)
    assert pub.destinations_for(_alert(5)) == ("alerts",)
    assert pub.destinations_for(_alert(7)) == ("alerts", "main")


def test_severity_never_raises_on_junk_metadata():
    assert pub.severity({"metadata": {"risk": "not-a-number"}}) == 0
    assert pub.severity({}) == 0


@pytest.mark.asyncio
async def test_ranking_is_risk_first_and_survives_a_broken_rerank(monkeypatch):
    alerts = [_alert(3, "a"), _alert(9, "b"), _alert(5, "c")]
    import sys
    import types

    broken = types.ModuleType("broken_rerank")

    async def _boom(**_kwargs):
        raise RuntimeError("no rerank")

    broken.rugbrain_search = _boom
    monkeypatch.setitem(sys.modules, "rugmunchbot._vendor.rugbrain_client", broken)
    out = await pub.rank_alerts(alerts)
    assert [a["id"] for a in out] == ["b", "c", "a"]
