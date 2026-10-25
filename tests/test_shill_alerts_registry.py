"""Tests: the registry adapter (one place, three schemas) and its degradation."""

from __future__ import annotations

import json

from rugmunchbot.channels.shill_alerts.registry import Channel, load_registry


def test_coverage_lane_text_list_is_read(tmp_path):
    path = tmp_path / "tg_channels.txt"
    path.write_text("# discovered\nDarkyCalls\n@thepumpalert\n\n", encoding="utf-8")
    rows = load_registry(path)
    assert [c.handle for c in rows] == ["darkycalls", "thepumpalert"]
    assert {c.origin for c in rows} == {"coverage-lane"}


def test_vendor_json_schema_is_read(tmp_path):
    path = tmp_path / "tg_channels.json"
    path.write_text(
        json.dumps(
            {
                "channels": [
                    {
                        "username": "@ShillPromos",
                        "name": "Shill Promos",
                        "kind": "scammer_caller",
                        "category": "shill_promotions",
                        "reliable": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    rows = load_registry(path)
    assert rows[0].handle == "shillpromos"
    assert rows[0].shill_prone is True
    assert rows[0].trust == 0.3
    assert rows[0].monitored_only is True


def test_channel_trust_schema_is_read_through_the_same_adapter(tmp_path):
    path = tmp_path / "channel_trust.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "channels": [
                    {
                        "handle": "darkycalls",
                        "title": "Darky Calls",
                        "class": "token-shiller",
                        "trust": 0.05,
                        "monitored_only": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    rows = load_registry(path)
    assert rows[0].handle == "darkycalls"
    assert rows[0].shill_prone is True
    assert rows[0].origin == "channel_trust"


def test_directory_argument_picks_the_first_known_file(tmp_path):
    (tmp_path / "tg_channels.txt").write_text("alpha\n", encoding="utf-8")
    assert [c.handle for c in load_registry(tmp_path)] == ["alpha"]


def test_absent_registry_degrades_to_empty(tmp_path):
    assert load_registry(tmp_path / "missing.txt") == []
    assert load_registry(tmp_path / "missing.json") == []


def test_schema_change_is_isolated_to_the_adapter(tmp_path):
    """An unknown JSON shape yields [] rather than raising in the lane."""
    path = tmp_path / "weird.json"
    path.write_text(json.dumps({"sources": [{"name": "x"}]}), encoding="utf-8")
    assert load_registry(path) == []


def test_channel_dataclass_defaults():
    channel = Channel(handle="x")
    assert channel.shill_prone is False
    assert channel.trust == 1.0
