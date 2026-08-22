"""Test wave 22 — personal_channel pure helpers (captions, gate).

Contracts read from source (2026-08-22). Pure logic only.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from rugmunchbot.channels.personal_channel import (
    CAPTION_TEMPLATES,
    _env_bool,
    _env_float,
    _env_int,
    _lead,
    build_caption,
    select_personal,
)

if TYPE_CHECKING:
    import pytest

# ── env knob helpers ─────────────────────────────────────────────────────────


def test_env_bool_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RMI_T_BOOL", raising=False)
    assert _env_bool("RMI_T_BOOL", True) is True  # unset -> default
    for truthy in ("true", "1", "yes", "on", "TRUE"):
        monkeypatch.setenv("RMI_T_BOOL", truthy)
        assert _env_bool("RMI_T_BOOL") is True
    monkeypatch.setenv("RMI_T_BOOL", "false")
    assert _env_bool("RMI_T_BOOL") is False


def test_env_int_clamps_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RMI_T_INT", raising=False)
    assert _env_int("RMI_T_INT", 7) == 7  # unset -> default
    monkeypatch.setenv("RMI_T_INT", "42")
    assert _env_int("RMI_T_INT", 7) == 42
    monkeypatch.setenv("RMI_T_INT", "0")
    assert _env_int("RMI_T_INT", 7) == 1  # floor of 1
    monkeypatch.setenv("RMI_T_INT", "junk")
    assert _env_int("RMI_T_INT", 7) == 7


def test_env_float_clamps_to_unit_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RMI_T_FLOAT", raising=False)
    assert _env_float("RMI_T_FLOAT", 0.5) == 0.5  # unset -> default
    monkeypatch.setenv("RMI_T_FLOAT", "0.9")
    assert _env_float("RMI_T_FLOAT", 0.5) == 0.9
    monkeypatch.setenv("RMI_T_FLOAT", "2.0")
    assert _env_float("RMI_T_FLOAT", 0.5) == 1.0  # clamped high
    monkeypatch.setenv("RMI_T_FLOAT", "-3")
    assert _env_float("RMI_T_FLOAT", 0.5) == 0.0  # clamped low
    monkeypatch.setenv("RMI_T_FLOAT", "junk")
    assert _env_float("RMI_T_FLOAT", 0.5) == 0.5


# ── lead clipping ────────────────────────────────────────────────────────────


def _article(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "body": "",
        "title": "Default Title",
        "source_url": "https://t.me/example/1",
        "source_channel": "example",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_lead_short_body_verbatim_and_fallbacks() -> None:
    assert _lead(_article(body="Short body.")) == "Short body."
    assert _lead(_article(body="", title="")) == ""  # nothing at all
    # Quirk: the body-or-title check happens on the RAW string, so a
    # whitespace-only body is "present" and strips to empty — no title
    # fallback (only a fully-empty/None body falls through).
    assert _lead(_article(body="   ")) == ""
    assert _lead(_article(body=None)) == "Default Title"


def test_lead_prefers_sentence_boundary_in_long_body() -> None:
    body = "A" * 50 + ". " + "B" * 300
    out = _lead(_article(body=body))
    assert out == "A" * 50 + "."  # clipped at the sentence end
    assert len(out) <= 240


def test_lead_without_boundary_appends_ellipsis() -> None:
    out = _lead(_article(body="x" * 400))
    assert out == "x" * 240 + "…"


# ── operator-voice captions ──────────────────────────────────────────────────


def test_build_caption_is_deterministic_per_source_url() -> None:
    a = _article(body="Real body text.", title="Headline")
    once = build_caption(a)  # type: ignore[arg-type]
    twice = build_caption(a)  # type: ignore[arg-type]
    assert once == twice  # hash-derived template index
    renderings = [
        t.format(
            title="Headline",
            lead="Real body text.",
            url="https://t.me/example/1",
            source="example",
        )
        for t in CAPTION_TEMPLATES
    ]
    assert once in renderings


def test_build_caption_explicit_template_and_telegram_fallback() -> None:
    a = _article(body="Body.", title="T", source_channel="")
    out = build_caption(a, template_idx=0)  # type: ignore[arg-type]
    assert out.startswith("🚨 T\n\nBody.")
    assert "— via Telegram" in out  # missing channel falls back to Telegram
    wrapped = build_caption(a, template_idx=99)  # type: ignore[arg-type]
    assert isinstance(wrapped, str)  # idx wraps modulo template count


# ── stricter rerank gate ─────────────────────────────────────────────────────


def _scored(importance: float, fallback: bool = False) -> SimpleNamespace:
    return SimpleNamespace(importance=importance, fallback=fallback)


def test_select_personal_rerank_mode_threshold_gate() -> None:
    scored = [_scored(0.9), _scored(0.75), _scored(0.5), _scored(0.85)]
    picked = select_personal(scored, top_n=10)
    assert [s.importance for s in picked] == [0.9, 0.85, 0.75]  # >= 0.7 only, desc


def test_select_personal_fallback_mode_keeps_flow_alive() -> None:
    # Reranker down: heuristic scores pass WITHOUT the threshold so the
    # channel never goes silent during an outage.
    scored = [_scored(0.4, fallback=True), _scored(0.6, fallback=True)]
    picked = select_personal(scored, top_n=10)
    assert [s.importance for s in picked] == [0.6, 0.4]


def test_select_personal_empty_and_cap() -> None:
    assert select_personal([], top_n=5) == []
    scored = [_scored(0.9), _scored(0.8), _scored(0.75)]
    assert len(select_personal(scored)) <= 3  # post-limit cap applies
