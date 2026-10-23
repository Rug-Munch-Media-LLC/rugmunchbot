"""Contract tests for project-level scam alerts (2026-10-24).

Oracle note (AI-TEST-ORACLE-POLICY): drives the real builder, renderer and
publisher against the FICTIONAL fixture project and asserts exact outcomes —
no sourced evidence means no alert, every card carries
why/who/source/timestamp/confidence/disclaimer, project+evidence dedupes, and
dry-run is the default so a rehearsal never reaches Telegram.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rugmunchbot.channels.scam_alerts._project_alert import (
    DISCLAIMER,
    build_project_alert,
)
from rugmunchbot.channels.scam_alerts._project_dedupe import (
    alert_fingerprint,
    is_duplicate,
    ledger_path,
    record,
)
from rugmunchbot.channels.scam_alerts._project_publish import (
    dry_run_enabled,
    publish_project_alert,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "scam_project_evidence.json").read_text(encoding="utf-8")
)
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def build(**overrides):
    """Build from the fixture with per-test overrides."""
    return build_project_alert(
        overrides.get("project", FIXTURE["project"]),
        overrides.get("evidence", FIXTURE["evidence"]),
        overrides.get("pushers", FIXTURE["pushers"]),
        now=NOW,
    )


# ── the evidence gate: no accusation without evidence ───────────────────────
def test_no_evidence_builds_nothing() -> None:
    """Empty evidence → no alert at all."""
    assert build_project_alert(FIXTURE["project"], [], [], now=NOW) is None


def test_unsourced_and_unknown_evidence_is_dropped() -> None:
    """A claim with no source, or an unrecognised kind, cannot become an alert."""
    junk = [
        {"kind": "honeypot", "detail": "sells blocked", "source": ""},
        {"kind": "vibes", "detail": "feels scammy", "source": "@somewhere"},
        {"kind": "honeypot", "source": "@somewhere"},
    ]
    assert build_project_alert(FIXTURE["project"], junk, [], now=NOW) is None


def test_anonymous_project_is_not_posted() -> None:
    """No name, symbol or address → nothing to identify, no alert."""
    assert build_project_alert({}, FIXTURE["evidence"], [], now=NOW) is None


# ── the card: why / who / source / timestamp / confidence / disclaimer ─────
def test_alert_has_full_evidence_contract() -> None:
    alert = build()
    assert alert is not None
    assert alert["severity"] == "critical"
    assert alert["why"][0]["kind"] == "honeypot"
    assert all(item["source"] for item in alert["why"])
    handles = [p["handle"] for p in alert["pushers"]]
    assert "@fixture_pump_channel" in handles
    assert handles.count("@fixture_pump_channel") == 1, "duplicate pusher must collapse"
    assert alert["timestamp"] == "2026-10-24T12:00:00+00:00"
    assert alert["first_seen"] == "2026-10-20T09:15:00+00:00"
    assert alert["disclaimer"] == DISCLAIMER


def test_confidence_tracks_evidence_strength() -> None:
    """Protocol proof outranks a lone social claim — never inflated."""
    strong = build(evidence=[{"kind": "honeypot", "detail": "sells revert", "source": "onchain:x"}])
    weak = build(
        evidence=[{"kind": "paid_shill_campaign", "detail": "paid promo", "source": "@chan"}]
    )
    assert strong["confidence"]["score"] == pytest.approx(0.80)
    assert strong["confidence"]["label"] == "high"
    assert weak["confidence"]["score"] == pytest.approx(0.40)
    assert weak["confidence"]["label"] == "low"
    corroborated = build()
    assert corroborated["confidence"]["score"] > strong["confidence"]["score"]


def test_addresses_are_lowercased_and_deduped() -> None:
    alert = build()
    addresses = [a["address"] for a in alert["project"]["addresses"]]
    assert addresses == ["0x000000000000000000000000000000000000f1c7"]


def test_render_carries_every_required_section() -> None:
    from rugmunchbot.channels.scam_alerts._project_render import render_project_alert

    post = render_project_alert(build())
    for needle in (
        "CRITICAL SCAM ALERT",
        "Why this is a scam",
        "Who is pushing it",
        "@fixture_pump_channel",
        "Deployer",
        "Confidence:",
        "First seen:",
        "Source",
        "not a legal finding",
        "/scan",
    ):
        assert needle in post, f"missing {needle!r}"


def test_render_escapes_external_strings() -> None:
    from rugmunchbot.channels.scam_alerts._project_render import render_project_alert

    alert = build(
        evidence=[
            {
                "kind": "drainer",
                "detail": "<script>alert(1)</script>",
                "source": "https://x.test/a?b=1&c=2",
            }
        ]
    )
    post = render_project_alert(alert)
    assert "<script>" not in post
    assert "&lt;script&gt;" in post


# ── dedupe: same project + same evidence = never twice ────────────────────
def test_fingerprint_is_order_independent_and_evidence_sensitive() -> None:
    alert = build()
    shuffled = {**alert, "why": list(reversed(alert["why"]))}
    assert alert_fingerprint(alert) == alert_fingerprint(shuffled)

    more = build(
        evidence=[
            *FIXTURE["evidence"],
            {"kind": "lp_pull", "detail": "LP removed 4 min after launch", "source": "onchain:lp"},
        ]
    )
    assert alert_fingerprint(more) != alert_fingerprint(alert)


def test_ledger_paths_separate_dry_run_from_live(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RMI_SCAM_PROJECT_LEDGER", str(tmp_path / "live.jsonl"))
    monkeypatch.setenv("RMI_SCAM_PROJECT_DRYRUN_LEDGER", str(tmp_path / "dry.jsonl"))
    assert ledger_path(True) != ledger_path(False)
    record("abc", build(), ledger_path(False))
    assert is_duplicate("abc", ledger_path(False))
    assert not is_duplicate("abc", ledger_path(True))


# ── publish: dry-run by default, never posts without evidence ─────────────
class _ExplodingBot:
    """Any send attempt fails the test — dry-run must not touch it."""

    async def send_message(self, *args, **kwargs):
        raise AssertionError("dry-run attempted a Telegram send")

    async def send_photo(self, *args, **kwargs):
        raise AssertionError("dry-run attempted a Telegram send")


async def test_publish_defaults_to_dry_run_and_dedupes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RMI_SCAM_PROJECT_DRYRUN_LEDGER", str(tmp_path / "dry.jsonl"))
    monkeypatch.setenv("RMI_SCAM_PROJECT_DRYRUN_OUT", str(tmp_path / "out.jsonl"))
    monkeypatch.delenv("RMI_SCAM_PROJECT_DRY_RUN", raising=False)
    assert dry_run_enabled() is True

    assert (await publish_project_alert(_ExplodingBot(), None))["status"] == "skipped_no_alert"
    alert = build()
    first = await publish_project_alert(_ExplodingBot(), alert, chat_id="-100test")
    assert first["status"] == "dry_run"
    assert "Why this is a scam" in first["post"]
    assert (tmp_path / "out.jsonl").exists()

    second = await publish_project_alert(_ExplodingBot(), alert, chat_id="-100test")
    assert second["status"] == "duplicate"


async def test_live_mode_without_channel_records_nothing(tmp_path, monkeypatch) -> None:
    """A missing destination is not a publish — the ledger must stay clean."""
    monkeypatch.setenv("RMI_SCAM_PROJECT_LEDGER", str(tmp_path / "live.jsonl"))
    monkeypatch.setenv("RMI_SCAM_PROJECT_CHANNEL_ROLE", "no_such_role")
    alert = build()
    result = await publish_project_alert(_ExplodingBot(), alert, chat_id="", dry_run=False)
    assert result["status"] == "no_channel"
    assert not is_duplicate(alert_fingerprint(alert), tmp_path / "live.jsonl")
