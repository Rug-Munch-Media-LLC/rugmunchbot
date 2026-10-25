"""Fixture tests for the voice scorer itself — the scorer must be able to fail."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import score  # noqa: E402

GOOD = {
    "id": "fix-good", "category": "security", "serious": True,
    "current": (
        "🔴 Liquidity removed from $SCAM\n"
        "The deployer withdrew 100% of the pool. Holders cannot exit at any price.\n"
        "Severity: CRITICAL • Chain: solana\n"
        "Full story: https://rugmunch.io/story/scam-lp-pull"
    ),
}

CLICHE = {
    "id": "fix-cliche", "category": "content", "serious": False,
    "current": (
        "In today's fast-paced crypto landscape, it's important to note that "
        "navigating the ever-evolving market is a game-changer. Let's dive in."
    ),
}

JOKING_LOSS = {
    "id": "fix-joking-loss", "category": "loss", "serious": True,
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
    assert subprocess.run(cmd + ["--min", "1.01"], capture_output=True).returncode != 0
    assert subprocess.run(cmd + ["--min", "0"], capture_output=True).returncode == 0
