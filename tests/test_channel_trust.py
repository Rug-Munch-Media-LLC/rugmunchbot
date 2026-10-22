"""Tests for the channel trust registry (data/channel_trust.json + loader)."""

from __future__ import annotations

import json

import pytest

from rugmunchbot.channels.channel_trust import (
    DEFAULT_PATH,
    load_channel_trust,
    normalize,
)


@pytest.fixture(scope="module")
def registry():
    return load_channel_trust()


def test_registry_file_ships_with_the_repo():
    assert DEFAULT_PATH.is_file(), f"missing registry at {DEFAULT_PATH}"
    payload = json.loads(DEFAULT_PATH.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    handles = {c["handle"] for c in payload["channels"]}
    assert {"darkycalls", "danny_crypton", "news_crypto"} <= handles


def test_token_shiller_is_never_a_news_source(registry):
    entry = registry.get("@DarkyCalls")
    assert entry.klass == "token-shiller"
    assert entry.news_eligible is False
    assert entry.weight == 0.0
    assert entry.monitored_only is True
    assert registry.news_sources(["darkycalls", "peckshield"]) == ["peckshield"]


def test_macro_clickbait_is_eligible_but_downweighted(registry):
    entry = registry.get("danny_crypton")
    assert entry.klass == "macro-clickbait"
    assert entry.news_eligible is True
    assert entry.weight == pytest.approx(entry.trust * 0.5)
    assert entry.weight < entry.trust
    assert entry.monitored_only is True


def test_aggregator_keeps_its_trust_and_is_citable(registry):
    entry = registry.get("news_crypto")
    assert entry.klass == "aggregator"
    assert entry.news_eligible is True
    assert entry.weight == pytest.approx(entry.trust)
    assert entry.monitored_only is False


def test_unknown_handle_is_neutral(registry):
    entry = registry.get("some_channel_not_in_the_registry")
    assert entry.klass == "unknown"
    assert entry.news_eligible is True
    assert entry.weight == 1.0
    assert entry.monitored_only is False


def test_evidence_is_quoted_with_a_link(registry):
    for handle in ("darkycalls", "danny_crypton"):
        evidence = registry.get(handle).evidence
        assert len(evidence) >= 2, f"{handle} needs at least two quoted posts"
        for item in evidence:
            assert item["text"].strip()
            assert item["link"].startswith("https://")


def test_normalize_accepts_handles_and_urls():
    assert normalize("@DarkyCalls") == "darkycalls"
    assert normalize("https://t.me/darkycalls/") == "darkycalls"
    assert normalize("  ") == ""


def test_missing_registry_fails_open(tmp_path):
    empty = load_channel_trust(tmp_path / "absent.json")
    assert empty.entries == {}
    assert empty.news_eligible("darkycalls") is True
    assert empty.weight("darkycalls") == 1.0
