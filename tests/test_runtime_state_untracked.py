"""Runtime moderation state must stay untracked and be created on demand.

Regression for the merge conflict caused by committing
``data/moderation_state.json`` and ``data/moderation/audit.jsonl``: a test run
writes both files, so a tracked copy dirties (and conflicts) every tree.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from rugmunchbot.community import moderation_audit
from rugmunchbot.moderation import persist

ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git") or "git"
RUNTIME_STATE = ("data/moderation_state.json", "data/moderation/audit.jsonl")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    """Fixed argv, fixed cwd — no interpolated input (hence the scoped noqa)."""
    return subprocess.run(  # noqa: S603
        (GIT, *args), cwd=ROOT, capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize("rel", RUNTIME_STATE)
def test_runtime_state_is_ignored_and_untracked(rel: str) -> None:
    assert _git("check-ignore", "--no-index", "-q", rel).returncode == 0, f"{rel} not gitignored"
    assert _git("ls-files", "--error-unmatch", rel).returncode != 0, f"{rel} is still tracked"


def test_state_snapshot_is_created_on_demand(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deeper" / "moderation_state.json"
    persist.use_path(target)
    try:
        assert persist.save("raid", {"1": [0, "probe"]}) is True
        assert target.is_file() is True
    finally:
        persist.use_path(ROOT / "data" / "moderation_state.json")


def test_audit_trail_is_created_on_demand(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deeper" / "audit.jsonl"
    moderation_audit.JsonlAudit(target).append({"action": "probe"})
    assert target.is_file() is True
