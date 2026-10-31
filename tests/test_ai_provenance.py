"""Behaviour pin for scripts/ci/ai-provenance.py (the AI provenance/risk gate).

Each case runs the real script against a throwaway git repo, so the risk rule,
the comment block and the GITHUB_OUTPUT keys are pinned end to end.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci" / "ai-provenance.py"
GIT = shutil.which("git") or "git"


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """A git environment that ignores whatever the hook runner exported."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "PYTHON"))}
    clean.update(PATH="/usr/bin:/bin", HOME=str(tmp_path), **extra)
    return clean


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted input
        (GIT, "-c", "user.email=t@t", "-c", "user.name=t", "-c", "core.hooksPath=/dev/null", *args),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=_env(repo.parent),
    )
    return done.stdout


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    home = tmp_path / "repo"
    home.mkdir()
    _git(home, "init", "-q")
    (home / "src").mkdir()
    (home / "docs").mkdir()
    (home / "README.md").write_text("hello\n", encoding="utf-8")
    (home / "src" / "app.py").write_text("a\n", encoding="utf-8")
    (home / "docs" / "guide.md").write_text("b\n", encoding="utf-8")
    _commit(home, "base")
    return home, _git(home, "rev-parse", "HEAD").strip()


def _run(repo: Path, base: str, tmp_path: Path, *extra: str) -> tuple[int, dict[str, str], str]:
    gh_out, block_out = tmp_path / "gh", tmp_path / "block"
    done = subprocess.run(  # noqa: S603 - argv list, no shell, trusted input
        [
            sys.executable,
            str(SCRIPT),
            "--base",
            base,
            "--head",
            "HEAD",
            "--out",
            str(block_out),
            *extra,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_env(tmp_path, GITHUB_OUTPUT=str(gh_out)),
    )
    outputs = dict(line.split("=", 1) for line in gh_out.read_text(encoding="utf-8").splitlines())
    return done.returncode, outputs, block_out.read_text(encoding="utf-8")


def test_high_risk_without_tests_blocks(repo: tuple[Path, str], tmp_path: Path) -> None:
    home, base = repo
    (home / "src" / "auth").mkdir()
    (home / "src" / "auth" / "login.py").write_text("x\n" * 60, encoding="utf-8")
    _commit(home, "feat(auth): login\n\nAI-Authored: yes\nAI-Model: v4-flash\nAI-Agent: junior-dev")

    code, outputs, block = _run(home, base, tmp_path)

    assert code == 1
    assert outputs["blocking"] == "true"
    assert outputs["ai_authored"] == "yes"
    assert outputs["risk_level"] == "high"
    assert int(outputs["risk_score"]) >= 70
    assert "| mode | BLOCKING |" in block
    assert "**Blocked:**" in block
    assert "v4-flash" in block
    assert "junior-dev" in block


def test_docs_only_stays_advisory(repo: tuple[Path, str], tmp_path: Path) -> None:
    home, base = repo
    (home / "docs" / "guide.md").write_text("more\n", encoding="utf-8")
    _commit(home, "docs: only")

    code, outputs, block = _run(home, base, tmp_path)

    assert code == 0
    assert outputs["blocking"] == "false"
    assert outputs["review_needed"] == "false"
    assert outputs["risk_score"] == "0"
    assert "| mode | advisory |" in block


def test_source_change_without_high_risk_is_advisory(
    repo: tuple[Path, str], tmp_path: Path
) -> None:
    home, base = repo
    (home / "src" / "app.py").write_text("a\nb\n", encoding="utf-8")
    _commit(home, "feat: tweak")

    code, outputs, _ = _run(home, base, tmp_path)

    assert code == 0
    assert outputs["blocking"] == "false"
    assert outputs["review_needed"] == "true"
    assert outputs["risk_level"] == "low"


def test_advisory_mode_never_blocks(repo: tuple[Path, str], tmp_path: Path) -> None:
    home, base = repo
    (home / "src" / "billing").mkdir()
    (home / "src" / "billing" / "charge.py").write_text("y\n" * 60, encoding="utf-8")
    _commit(home, "feat(billing): charge")

    code, outputs, block = _run(home, base, tmp_path, "--mode", "advisory")

    assert code == 0
    assert outputs["blocking"] == "false"
    assert outputs["risk_level"] == "high"
    assert "| mode | advisory |" in block


def test_high_risk_with_test_escapes_the_gap(repo: tuple[Path, str], tmp_path: Path) -> None:
    home, base = repo
    (home / "src" / "auth").mkdir()
    (home / "src" / "auth" / "login.py").write_text("x\n", encoding="utf-8")
    (home / "tests").mkdir()
    (home / "tests" / "test_login.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    _commit(home, "feat(auth): login with a test")

    code, outputs, block = _run(home, base, tmp_path)

    assert code == 0
    assert outputs["blocking"] == "false"
    assert "| mode | advisory |" in block


def test_empty_diff_is_low_and_advisory(repo: tuple[Path, str], tmp_path: Path) -> None:
    home, base = repo

    code, outputs, block = _run(home, base, tmp_path)

    assert code == 0
    assert outputs["risk_score"] == "10"
    assert outputs["risk_level"] == "low"
    assert outputs["review_needed"] == "false"
    assert "| mode | advisory |" in block
