"""Fixture tests for the voice scorer - the scorer must be able to fail.

The calibration seam is contracted here too: the shipped default is the
uniform example (never the fleet calibration), and an injected calibration
file must be complete, positive and unit-summing or the loader fails loud.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import policy  # noqa: E402
import pytest  # noqa: E402
import rules  # noqa: E402
import score  # noqa: E402

GOOD = {
    "id": "fix-good",
    "category": "security",
    "serious": True,
    "current": (
        "🔴 Liquidity removed from $SCAM\n"
        "The deployer withdrew 100% of the pool. Holders cannot exit at any price.\n"
        "Severity: CRITICAL • Chain: solana\n"
        "Full story: https://rugmunch.io/story/scam-lp-pull"
    ),
}

CLICHE = {
    "id": "fix-cliche",
    "category": "content",
    "serious": False,
    "current": (
        "In today's fast-paced crypto landscape, it's important to note that "
        "navigating the ever-evolving market is a game-changer. Let's dive in."
    ),
}

JOKING_LOSS = {
    "id": "fix-joking-loss",
    "category": "loss",
    "serious": True,
    "current": "Liquidation hit. lol better luck next time anon 😂",
}


def test_good_sample_passes():
    """A well-built serious alert clears the target."""
    assert score.score_sample(GOOD)["score"] >= 0.75


def test_cliche_sample_fails():
    """Marketing filler is caught by the cliché rule, not by taste."""
    r = score.score_sample(CLICHE)
    assert r["subs"]["cliche"] < 0.5
    assert r["score"] < 0.75


def test_joking_loss_message_fails():
    """A joke inside a loss message zeroes the register rule outright."""
    r = score.score_sample(JOKING_LOSS)
    assert r["serious"] is True
    assert r["subs"]["register"] == 0.0
    assert r["score"] < 0.75


def test_gate_exit_codes(tmp_path):
    """--min above the aggregate exits non-zero; --min 0 exits zero."""
    cmd = [sys.executable, str(HERE / "score.py")]
    # Repo-local command, no untrusted input: bandit S603 is silenced per line.
    assert subprocess.run([*cmd, "--min", "1.01"], capture_output=True).returncode != 0  # noqa: S603
    assert subprocess.run([*cmd, "--min", "0"], capture_output=True).returncode == 0  # noqa: S603


def test_example_calibration_is_uniform():
    """The shipped default is the uniform shape - never the fleet calibration."""
    w = policy.example_weights()
    assert set(w) == set(rules.SUBSCORE_NAMES)
    assert len(set(w.values())) == 1
    assert abs(sum(w.values()) - 1.0) < 1e-9


def test_load_weights_defaults_to_example(monkeypatch):
    """No env: the eval runs on the uniform example, labelled as such."""
    monkeypatch.delenv(policy.ENV_FILE, raising=False)
    cal = policy.load_weights()
    assert cal.weights == policy.example_weights()
    assert "example" in cal.source


def test_load_weights_reads_private_file(tmp_path, monkeypatch):
    """RMB_VOICE_WEIGHTS_FILE injects the real calibration over the interface."""
    path = tmp_path / "voice-calibration.json"
    path.write_text(json.dumps(dict.fromkeys(rules.SUBSCORE_NAMES, 1.0 / 7)))
    monkeypatch.setenv(policy.ENV_FILE, str(path))
    cal = policy.load_weights()
    assert set(cal.weights) == set(rules.SUBSCORE_NAMES)
    assert cal.source == str(path)


def test_load_weights_rejects_incomplete_calibration(tmp_path, monkeypatch):
    """A calibration missing a sub-score fails loud - no silent fallback."""
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(dict.fromkeys(rules.SUBSCORE_NAMES[:3], 1.0 / 3)))
    monkeypatch.setenv(policy.ENV_FILE, str(path))
    with pytest.raises(ValueError, match="exactly"):
        policy.load_weights()


def test_load_weights_rejects_bad_sum(tmp_path, monkeypatch):
    """A calibration that does not sum to 1.0 is rejected, not coerced."""
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(dict.fromkeys(rules.SUBSCORE_NAMES, 0.1)))
    monkeypatch.setenv(policy.ENV_FILE, str(path))
    with pytest.raises(ValueError, match="sum"):
        policy.load_weights()


def test_score_sample_accepts_injected_calibration():
    """score_sample stays a pure function: the calibration is explicit."""
    uniform = policy.example_weights()
    spread_only = dict.fromkeys(rules.SUBSCORE_NAMES, 0.0)
    spread_only["spread"] = 1.0
    r = score.score_sample(GOOD, uniform)
    assert score.score_sample(GOOD, spread_only)["score"] == pytest.approx(r["subs"]["spread"])
