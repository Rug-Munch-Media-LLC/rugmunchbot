"""ct_shill_watch — unit coverage for the split modules (no global patching).

Success, failure and boundary paths for the registry/rotation cursor, the
wall-of-shame writer, the severity ladder and the retired scheduled ticks.
Every path is injected, so these tests never mutate import-time config.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rugmunchbot.channels import ct_shill_watch as m
from rugmunchbot.channels.ct_shill_watch import _registry, _runner, _wall

# ── registry ────────────────────────────────────────────────────────────────


def test_registry_normalizes_and_dedups(tmp_path):
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps({"handles": ["@AlphaKol", "alphakol", {"handle": " CTGURU "}, ""]}))
    assert _registry.load_ct_handles(str(reg)) == ["alphakol", "ctguru"]


def test_registry_reads_accounts_key(tmp_path):
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps({"accounts": ["solo"]}))
    assert _registry.load_ct_handles(str(reg)) == ["solo"]


def test_registry_invalid_json_is_fail_open(tmp_path):
    reg = tmp_path / "reg.json"
    reg.write_text("{not json")
    assert _registry.load_ct_handles(str(reg)) == []


def test_registry_missing_file_is_fail_open(tmp_path):
    assert _registry.load_ct_handles(str(tmp_path / "nope.json")) == []


# ── rotation cursor ─────────────────────────────────────────────────────────


def test_batch_count_above_length_returns_all_once(tmp_path):
    cursor = str(tmp_path / "c.json")
    handles = ["a", "b", "c"]
    assert _registry.next_account_batch(handles, 10, cursor) == handles
    # cursor wrapped to 0 → next call starts at the head again
    assert _registry.next_account_batch(handles, 1, cursor) == ["a"]


def test_batch_non_positive_count_is_empty_and_keeps_cursor(tmp_path):
    cursor = str(tmp_path / "c.json")
    assert _registry.next_account_batch(["a", "b"], 0, cursor) == []
    assert _registry.next_account_batch(["a", "b"], -3, cursor) == []
    assert not Path(cursor).exists()


def test_corrupt_cursor_restarts_at_head(tmp_path):
    cursor = tmp_path / "c.json"
    cursor.write_text("not-json")
    assert _registry.next_account_batch(["a", "b"], 1, str(cursor)) == ["a"]


# ── wall writer ─────────────────────────────────────────────────────────────


def test_record_shill_missing_wall_is_fail_open(tmp_path):
    assert _wall.record_shill("h", token="$X", wall_path=str(tmp_path / "missing.json")) is False


def test_record_shill_corrupt_wall_is_fail_open(tmp_path):
    wall = tmp_path / "wall.json"
    wall.write_text("{broken")
    assert _wall.record_shill("h", token="$X", wall_path=str(wall)) is False


def test_record_shill_captures_wallet_on_existing_entry(tmp_path):
    wall = tmp_path / "wall.json"
    wall.write_text(json.dumps({"wall_of_shame": []}))
    _wall.record_shill("h", token="$A", wall_path=str(wall))
    assert _wall.record_shill("h", token="$B", wallet="0xabc", wall_path=str(wall))
    entry = json.loads(wall.read_text())["wall_of_shame"][0]
    assert entry["wallet"] == "0xabc"
    assert entry["wallets"] == ["0xabc"]


def test_record_shill_identical_repeat_does_not_write(tmp_path):
    """Contract: the file is written only when the entry actually changes."""
    wall = tmp_path / "wall.json"
    wall.write_text(json.dumps({"wall_of_shame": []}))
    _wall.record_shill("h", token="$A", tweet_text="same", wall_path=str(wall))
    before = wall.read_text()
    assert _wall.record_shill("h", token="$A", tweet_text="same", wall_path=str(wall)) is False
    assert wall.read_text() == before


# ── scoring ─────────────────────────────────────────────────────────────────


def test_score_kol_empty_entry_is_zero():
    assert m.score_kol({}) == 0.0


def test_score_kol_recency_decays_old_shills():
    old = {"tokens_shilled": ["$A"], "shill_count": 1, "last_shill_ts": "2000-01-01T00:00:00+00:00"}
    fresh = {"tokens_shilled": ["$A"], "shill_count": 1}
    assert m.score_kol(fresh) > m.score_kol(old)
    assert m.score_kol(old) > 0


def test_score_kol_invalid_timestamp_uses_half_weight():
    bad = {"tokens_shilled": ["$A"], "shill_count": 1, "last_shill_ts": "not-a-date"}
    fresh = {"tokens_shilled": ["$A"], "shill_count": 1}
    assert m.score_kol(bad) < m.score_kol(fresh)


def _wall_doc(entries: list[dict]) -> str:
    return json.dumps({"updated": "", "count": len(entries), "wall_of_shame": entries})


def test_rescore_ladder_high_medium_low(tmp_path):
    wall = tmp_path / "wall.json"
    wall.write_text(
        _wall_doc(
            [
                {
                    "source": "ct_shill_watch",
                    "handle": "a",
                    "tokens_shilled": ["1"] * 6,
                    "shill_count": 6,
                },
                {
                    "source": "ct_shill_watch",
                    "handle": "b",
                    "tokens_shilled": ["1"],
                    "shill_count": 1,
                },
                {"source": "ct_shill_watch", "handle": "c", "tokens_shilled": [], "shill_count": 0},
            ]
        )
    )
    changed = _wall.rescore_wall_of_shame(str(wall))
    by_handle = {e["handle"]: e for e in json.loads(wall.read_text())["wall_of_shame"]}
    assert (by_handle["a"]["severity"], by_handle["a"]["risk_level"]) == ("high", "critical")
    assert (by_handle["b"]["severity"], by_handle["b"]["risk_level"]) == ("medium", "high")
    assert (by_handle["c"]["severity"], by_handle["c"]["risk_level"]) == ("low", "watch")
    assert changed == 3  # every entry gains a severity/risk pair


def test_rescore_skips_entries_it_does_not_own(tmp_path):
    wall = tmp_path / "wall.json"
    wall.write_text(_wall_doc([{"handle": "foreign", "severity": "unknown"}]))
    assert _wall.rescore_wall_of_shame(str(wall)) == 0
    entry = json.loads(wall.read_text())["wall_of_shame"][0]
    assert entry == {"handle": "foreign", "severity": "unknown"}


def test_rescore_missing_wall_is_fail_open(tmp_path):
    assert _wall.rescore_wall_of_shame(str(tmp_path / "missing.json")) == 0


# ── scheduled ticks ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retired_ticks_are_zero_noops():
    """Contract invariant: X ingestion is rugbrain's; both ticks stay no-ops."""
    assert await _runner.run_ct_shill_watch() == 0
    assert await _runner.run_ct_shill_watch(object()) == 0
    assert await _runner.discover_from_retweeters() == 0


# ── facade contract ─────────────────────────────────────────────────────────


def test_facade_reexports_every_public_name():
    for name in m.__all__:
        assert hasattr(m, name), name
    assert m.record_shill is _wall.record_shill
    assert m.run_ct_shill_watch is _runner.run_ct_shill_watch
    assert m.load_ct_handles is _registry.load_ct_handles


def test_facade_keeps_config_paths_importable():
    assert m.WALL_OF_SHAME_PATH.endswith("wall_of_shame.json")
    assert m.INFLUENTIAL_ACCOUNTS_PATH.endswith("influential_accounts.json")
