"""Test wave 4 — scanner_engines deployer-profile pure helpers.

Contracts read from source (2026-08-22). Pure mapping/formatting only:
no ClickHouse, no network.
"""

from __future__ import annotations

import pytest

pytest.importorskip("rugmunch_scanner")

from rugmunch_scanner.unified.engines.deployer_profiles import (
    _row_to_profile,
    build_deployer_label,
)

# ── build_deployer_label ─────────────────────────────────────────────────────


def test_label_full_track_record() -> None:
    label = build_deployer_label(
        {"total_tokens_launched": 15, "rugged_pct": 0.4, "avg_lifespan_days": 3}
    )
    assert label == "Repeat deployer: 15 tokens, 40% rugged, avg life 3 days"


def test_label_empty_profile_defaults() -> None:
    assert build_deployer_label({}) == ("Repeat deployer: 0 tokens, 0% rugged, avg life unknown")


def test_label_subday_lifespan_in_hours() -> None:
    label = build_deployer_label(
        {"total_tokens_launched": 2, "rugged_pct": 0.5, "avg_lifespan_days": 0.5}
    )
    assert label.endswith("avg life 12 hours")


# ── _row_to_profile ──────────────────────────────────────────────────────────


def _row(deployments: int = 4) -> dict[str, object]:
    return {
        "row": [
            "0xabc",  # deployer
            "eth",  # chain_id
            3,  # tokens
            deployments,  # deployments
            12500.0,  # liq
            0.33,  # rugged
            0.6,  # active
            0.1,  # honeypot
            2.5,  # lifespan
            ["eth"],  # chains
            "2026-01-01",
            "2026-02-01",
        ]
    }


def test_row_to_profile_maps_fields() -> None:
    profile = _row_to_profile(_row())
    assert profile is not None
    assert profile["result"] == "scanned"
    assert profile["deployer"] == "0xabc"
    assert profile["chain"] == "eth"
    assert profile["is_repeat_deployer"] is True
    assert profile["total_tokens_launched"] == 3
    assert profile["rugged_pct"] == 0.33


def test_row_to_profile_single_deployment_is_none() -> None:
    assert _row_to_profile(_row(deployments=1)) is None


def test_row_to_profile_missing_row_is_none() -> None:
    assert _row_to_profile({}) is None
