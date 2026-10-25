"""CT shill watch — timeline ingestion, confirmation, wall-of-shame recording.

Mocked transports only: no network, no X key required (fail-open paths
exercised directly). Covers:

* CT registry loading + rotation cursor (no starvation, wrap-around)
* token-promotion → confirmation gating (scanner / TG cross-ref)
* record_shill: new entry shape, idempotent increment, coordinated escalation
* score_kol + rescore severity ladder
* run_ct_shill_watch retired no-op (X ingestion owned by rugbrain)

Paths are injected per call (``path=``/``wall_path=``/``cursor_path=``) — no
global patching, so the tests do not depend on import-time config.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from rugmunchbot.channels import ct_shill_watch as m


@dataclass(frozen=True)
class KolPaths:
    """Isolated kol data dir: registry + wall + cursor files."""

    registry: str
    wall: str
    cursor: str


@pytest.fixture
def kol_dir(tmp_path) -> KolPaths:
    """Isolated kol data dir: shame file + cursor + registry."""
    (tmp_path / "influential_accounts.json").write_text(
        json.dumps({"overlap_with_ct": ["alphakol", "shillqueen", "ctguru"]})
    )
    wall = tmp_path / "wall_of_shame.json"
    wall.write_text(json.dumps({"updated": "", "count": 0, "wall_of_shame": []}))
    return KolPaths(
        registry=str(tmp_path / "influential_accounts.json"),
        wall=str(wall),
        cursor=str(tmp_path / "ct_cursor.json"),
    )


def _wall(paths: KolPaths) -> dict:
    return json.loads(Path(paths.wall).read_text())


# ══════════════════════════════════════════════════════════════════════════
# Registry + rotation
# ══════════════════════════════════════════════════════════════════════════


def test_load_ct_handles(kol_dir):
    handles = m.load_ct_handles(kol_dir.registry)
    assert handles == ["alphakol", "shillqueen", "ctguru"]


def test_load_ct_handles_fail_open(tmp_path):
    assert m.load_ct_handles(str(tmp_path / "missing.json")) == []


def test_rotation_cycles_without_starvation(kol_dir):
    handles = m.load_ct_handles(kol_dir.registry)
    seen: list[str] = []
    for _ in range(2):
        batch = m.next_account_batch(handles, 2, kol_dir.cursor)
        seen.extend(batch)
    assert set(seen) == set(handles)  # every account hit within two ticks
    assert len(seen) == 4  # batch of 2 x 2 ticks, no starvation


def test_next_account_batch_empty():
    assert m.next_account_batch([], 2) == []


# ══════════════════════════════════════════════════════════════════════════
# record_shill
# ══════════════════════════════════════════════════════════════════════════


def test_record_shill_new_entry(kol_dir):
    assert m.record_shill(
        "ShillQueen",
        token="$RUG",
        tweet_text="aping $RUG 100x",
        tweet_url="https://x.com/shillqueen/status/1",
        screenshot_url="shot.png",
        wall_path=kol_dir.wall,
    )
    data = _wall(kol_dir)
    entry = data["wall_of_shame"][0]
    assert entry["handle"] == "shillqueen"
    assert entry["category"] == "shill"
    assert entry["shill_count"] == 1
    assert entry["tokens_shilled"] == ["$RUG"]
    assert entry["evidence"][0]["screenshot_url"] == "shot.png"
    assert entry["platforms"] == ["x"]
    assert data["count"] == 1


def test_record_shill_increments_and_dedups_tokens(kol_dir):
    m.record_shill(
        "shillqueen", token="$RUG", tweet_text="a", tweet_url="u1", wall_path=kol_dir.wall
    )
    assert m.record_shill(
        "shillqueen", token="$RUG", tweet_text="b", tweet_url="u2", wall_path=kol_dir.wall
    )
    entry = _wall(kol_dir)["wall_of_shame"][0]
    assert entry["shill_count"] == 2
    assert entry["tokens_shilled"] == ["$RUG"]  # deduped
    assert len(entry["evidence"]) == 2


def test_record_shill_coordinated_escalates(kol_dir):
    m.record_shill("shillqueen", token="$RUG", tweet_text="a", wall_path=kol_dir.wall)
    m.record_shill(
        "shillqueen", token="$PEPE", coordinated=True, platform="telegram", wall_path=kol_dir.wall
    )
    entry = _wall(kol_dir)["wall_of_shame"][0]
    assert entry["coordinated"] is True
    assert entry["severity"] == "high"
    assert entry["risk_level"] == "critical"
    assert sorted(entry["platforms"]) == ["telegram", "x"]


def test_record_shill_rejects_empty_handle(kol_dir):
    assert not m.record_shill("", token="$RUG", wall_path=kol_dir.wall)


# ══════════════════════════════════════════════════════════════════════════
# Scoring
# ══════════════════════════════════════════════════════════════════════════


def test_score_kol_coordinated_outranks_solo():
    solo = {"tokens_shilled": ["$A"], "shill_count": 1, "coordinated": False}
    coord = {"tokens_shilled": ["$A"], "shill_count": 1, "coordinated": True}
    assert m.score_kol(coord) > m.score_kol(solo)


def test_rescore_escalates_serial_shills(kol_dir):
    for i in range(6):
        m.record_shill("shillqueen", token=f"$T{i}", tweet_text="x", wall_path=kol_dir.wall)
    changed = m.rescore_wall_of_shame(kol_dir.wall)
    entry = _wall(kol_dir)["wall_of_shame"][0]
    assert entry["shill_score"] >= 6
    assert entry["severity"] == "high"
    assert changed >= 1


# ══════════════════════════════════════════════════════════════════════════
# Confirmation
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_confirm_shill_tg_cross_hit(kol_dir):
    async def fake_evidence(account="", limit=50):
        return {"evidence": [{"source": "telegram", "account": account}]}

    with patch("rugmunchbot.services.shill_api.get_evidence", fake_evidence):
        confirmed, coordinated = await m.confirm_shill("shillqueen", "$RUG", "ticker")
    assert confirmed
    assert coordinated


@pytest.mark.asyncio
async def test_confirm_shill_scanner_risky(kol_dir):
    async def fake_evidence(account="", limit=50):
        return {"evidence": []}

    async def fake_risky(token, chain):
        return True

    with (
        patch("rugmunchbot.services.shill_api.get_evidence", fake_evidence),
        patch("rugmunchbot.channels.kol_discovery._is_risky_token", fake_risky),
    ):
        confirmed, coordinated = await m.confirm_shill("shillqueen", "0xabc", "ethereum")
    assert confirmed
    assert not coordinated


# ══════════════════════════════════════════════════════════════════════════
# The tick (end-to-end, patched transports)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_run_ct_shill_watch_retired_noop(kol_dir):
    """X ingestion is owned by rugbrain — the tick is a fail-open no-op."""
    assert await m.run_ct_shill_watch() == 0
