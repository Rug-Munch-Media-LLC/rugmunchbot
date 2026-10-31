"""Determinism contract for rugmunchbot.voice.render.

The voice is *specified*: identical input must produce identical output, in
this process and in the next one. These tests fail if variant selection ever
becomes random or process-salted.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from rugmunchbot.voice import VARIANTS, Register, register_for, render

REPO = Path(__file__).resolve().parents[1]


def _placeholders(template: str) -> list[str]:
    return re.findall(r"\{(\w+)\}", template)


def _full_context() -> dict[str, str]:
    names = {n for _reg, vs in VARIANTS.values() for v in vs for n in _placeholders(v)}
    return dict.fromkeys(names, "x")


def test_same_input_twice_is_identical() -> None:
    ctx = {"ticker": "$WIF"}
    first = render("cmd.watch_saved", ctx, message_id=4815162342)
    second = render("cmd.watch_saved", ctx, message_id=4815162342)
    assert first == second


def test_render_is_stable_across_processes() -> None:
    """A fresh interpreter must pick the same variant (no hash() salting)."""
    code = (
        "from rugmunchbot.voice import render;"
        "print(render('cmd.watch_saved', {'ticker': '$WIF'}, message_id=42))"
    )
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    out = subprocess.run(  # noqa: S603 - argv list, no shell, trusted input
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env
    ).stdout.strip()
    assert out == render("cmd.watch_saved", {"ticker": "$WIF"}, message_id=42)


def test_serious_keys_never_rotate() -> None:
    serious = [k for k, (reg, _) in VARIANTS.items() if reg is Register.SERIOUS]
    assert serious, "expected at least one SERIOUS key"
    for key in serious:
        outputs = {render(key, _full_context(), message_id=i) for i in range(50)}
        assert len(outputs) == 1, f"{key} rotated under SERIOUS"


def test_no_unfilled_placeholders() -> None:
    ctx = _full_context()
    for key, (_reg, _variants) in VARIANTS.items():
        assert any("{" not in render(key, ctx, message_id=i) for i in range(4)), key


def test_unknown_key_fails_loud() -> None:
    with pytest.raises(KeyError):
        render("cmd.does_not_exist", {}, message_id=1)


def test_register_prefix_inheritance() -> None:
    assert register_for("alert.rug.solana") is Register.SERIOUS
    assert register_for("alert.whale.sol") is Register.WITTY
    assert register_for("totally.unknown") is Register.NEUTRAL
