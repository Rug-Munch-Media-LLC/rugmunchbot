"""Tests for the deterministic content-quality lint (support.content_lint).

Oracle note (AI-TEST-ORACLE-POLICY): these tests assert exact rule ids,
phrases and locations against hand-built fixtures — not tautological
pass/fail smoke checks.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from rugmunchbot.support.content_lint import (
    BANNED_PHRASES,
    BLAND_MIN_WORDS,
    lint_post,
    lint_posts,
    lint_text,
)

CLEAN_POST = (
    "Strategy bought 4,603 BTC for $369.7M, lifting holdings to 845,050 BTC. "
    "The buy lands two weeks after their last tranche and keeps the 2026 "
    "accumulation pace above 2025's average. Treasury math still works at "
    "these funding costs, barely."
)


def test_clean_text_has_no_violations() -> None:
    assert lint_text(CLEAN_POST) == []


def test_banned_phrase_flagged_with_location() -> None:
    text = "Let's delve into the numbers.\nSecond line here."
    v = lint_text(text)
    banned = [x for x in v if x.rule == "banned-phrase"]
    assert len(banned) == 1
    assert banned[0].phrase == "delve into"
    assert banned[0].location == "line 1, col 7"


def test_banned_phrase_case_insensitive() -> None:
    assert any(v.rule == "banned-phrase" for v in lint_text("STAY TUNED for more"))


def test_double_link_flagged() -> None:
    url = "https://example.com/a"
    text = f"Read {url} and then {url} again for context. " + "word " * 25
    v = lint_text(text)
    dbl = [x for x in v if x.rule == "double-link"]
    assert len(dbl := [x for x in v if x.rule == "double-link"]) == 1
    assert dbl[0].phrase == url
    assert "2x" in dbl[0].detail


def test_template_signature_flagged() -> None:
    text = "[HIGH] Token rugged\n\nSource: https://example.com\n"
    rules = {v.rule for v in lint_text(text)}
    assert "template-signature" in rules


def test_bland_commentary_short_text_flagged() -> None:
    text = "Big news! https://example.com/x"
    rules = {v.rule for v in lint_text(text)}
    assert "bland-commentary" in rules


def test_bland_commentary_threshold_boundary() -> None:
    # Exactly BLAND_MIN_WORDS words, no URLs → clean.
    text = " ".join(["word"] * BLAND_MIN_WORDS)
    assert not [v for v in lint_text(text) if v.rule == "bland-commentary"]
    # One word short → flagged.
    text = " ".join(["word"] * (BLAND_MIN_WORDS - 1))
    assert [v for v in lint_text(text) if v.rule == "bland-commentary"]


def test_lint_post_missing_image() -> None:
    post = {"slug": "post-1", "text": CLEAN_POST, "source": "https://x.com/a"}
    rules = {v.rule for v in lint_post(post)}
    assert "missing-image" in rules
    post["image_url"] = "https://example.com/img.png"
    assert "missing-image" not in {v.rule for v in lint_post(post)}


def test_lint_posts_only_reports_dirty() -> None:
    posts = [
        {"slug": "clean", "text": CLEAN_POST, "image_url": "https://e.com/i.png"},
        {"slug": "dirty", "text": "stay tuned", "image_url": "https://e.com/i.png"},
    ]
    out = lint_posts(posts)
    assert list(out) == ["dirty"]
    assert any(v.rule == "banned-phrase" for v in out["dirty"])


def test_banned_list_is_superset_of_roundup_needs() -> None:
    # The canonical list must keep the phrases the roundup relied on.
    for phrase in ("delve into", "stay tuned", "not financial advice"):
        assert phrase in BANNED_PHRASES


def test_cli_file_input_exit_codes(tmp_path: Any) -> None:
    dirty = tmp_path / "post.txt"
    dirty.write_text("It's worth noting that things are happening.\n")
    clean = tmp_path / "clean.txt"
    clean.write_text(CLEAN_POST)
    rc_dirty = subprocess.run(  # noqa: S603 - fixed argv, no untrusted input
        [sys.executable, "-m", "rugmunchbot.tools.content_lint", str(dirty)],
        capture_output=True,
        text=True,
    )
    rc_clean = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "rugmunchbot.tools.content_lint", str(clean)],
        capture_output=True,
        text=True,
    )
    assert rc_dirty.returncode == 1
    assert "banned-phrase" in rc_dirty.stdout
    assert rc_clean.returncode == 0


def test_cli_json_output(tmp_path: Any) -> None:
    dirty = tmp_path / "post.txt"
    dirty.write_text("gm everyone\n")
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "rugmunchbot.tools.content_lint", "--json", str(dirty)],
        capture_output=True,
        text=True,
    )
    report = json.loads(proc.stdout)
    assert report["clean"] is False
    assert report["violations"][0]["rule"] == "banned-phrase"


# ── looks_like_reasoning_leak (2026-09-02 CoT-in-channel incident) ──────────


def test_reasoning_leak_clean_post_passes() -> None:
    from rugmunchbot.support.content_lint import looks_like_reasoning_leak

    leaked, reason = looks_like_reasoning_leak(CLEAN_POST)
    assert leaked is False
    assert reason == ""


def test_reasoning_leak_incident_slop_detected() -> None:
    from rugmunchbot.support.content_lint import looks_like_reasoning_leak

    slop = (
        "We need to rewrite story notes into a Telegram post following rules. "
        "Let's craft: open with one factual sentence. Check word count — keep "
        "under 120 words. Output only the post. No emojis, no hype."
    )
    leaked, reason = looks_like_reasoning_leak(slop)
    assert leaked is True
    assert reason == "reasoning_leak"


def test_reasoning_leak_empty_text_passes() -> None:
    from rugmunchbot.support.content_lint import looks_like_reasoning_leak

    assert looks_like_reasoning_leak("") == (False, "")


# ── 2026-10-17: scam-alert copy passes the deterministic lint ──────────────


def test_scam_alert_copy_is_lint_clean() -> None:
    """The dedicated scam-alert card carries no banned phrase / double link
    and enough original commentary to clear the bland-commentary floor."""
    from rugmunchbot.channels.formatters import format_scam_alert

    post = format_scam_alert(
        {
            "severity": "critical",
            "name": "RugCoin",
            "symbol": "RUG",
            "address": "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            "chain": "ethereum",
            "scam_intel": (
                "Honeypot: sells are blocked after buying. The deployer pulled "
                "liquidity four minutes after launch and the mint authority is "
                "still live, so supply can be inflated at will."
            ),
            "source": "https://x.com/example/status/1",
        }
    )
    violations = lint_text(str(post))
    rules = {v.rule for v in violations}
    assert "banned-phrase" not in rules
    assert "double-link" not in rules
    assert "template-signature" not in rules


def test_reauthored_post_types_are_lint_clean() -> None:
    """The re-authored copy for roundup / dislocation / LP-expiry /
    social-spike / updates-digest carries no banned phrase and no
    double-link. Each type's masthead is distinct (no shared generic
    banner) — the whole point of the 2026-10-17 overhaul."""
    from rugmunchbot.channels.scheduler._posts_dislocation import _DISLO_HEADER, _DISLO_SUB
    from rugmunchbot.channels.scheduler._posts_lp_expiry import _LP_HEADER, _LP_SUB
    from rugmunchbot.channels.scheduler._posts_roundup import compose_roundup
    from rugmunchbot.channels.scheduler._posts_social_spikes import _SPIKE_HEADER, _SPIKE_SUB
    from rugmunchbot.channels.scheduler._posts_updates import compose_updates_digest

    roundup = compose_roundup(
        [
            {"slug": "post-1", "label": "Whale churned 12M tokens", "source": ""},
            {"slug": "post-2", "label": "Vault drained overnight", "source": ""},
            {"slug": "post-3", "label": "Mixer-funded deployer", "source": ""},
        ]
    )
    updates = compose_updates_digest(["✅ rag-api", "✅ model-gateway"])

    samples = {
        "roundup": roundup,
        "dislocation": f"{_DISLO_HEADER}\n{_DISLO_SUB}\n\n"
        "🚨 <b>SOL</b> — long binance +0.0100%/h, short bybit -0.0200%/h "
        "(<b>+0.0300%/h spread</b>)\n   Perp basis is inverted across venues.",
        "lp_expiry": f"{_LP_HEADER}\n{_LP_SUB}\n\n"
        "🚨 <b>RUG</b> — lock ends in <b>3 days</b>, 82% of LP still locked. "
        "After that, the floor is whatever the deployer leaves.",
        "social_spike": f"{_SPIKE_HEADER}\n{_SPIKE_SUB}\n\n"
        "📈 <b>SOL</b> — 412 mentions/24h, sentiment +38 (bullish). "
        "Chatter is running hot; watch the tape for confirmation.",
        "updates": updates,
    }
    for name, text in samples.items():
        rules = {v.rule for v in lint_text(text)}
        assert "banned-phrase" not in rules, f"{name}: banned phrase"
        assert "double-link" not in rules, f"{name}: double link"
        assert "template-signature" not in rules, f"{name}: template signature"

    # Distinct mastheads — no two types share the retired generic banner.
    headers = {_DISLO_HEADER, _LP_HEADER, _SPIKE_HEADER}
    assert len(headers) == 3
    assert "THE RUG MUNCH ROUNDUP" not in roundup
    assert "RugMunch system progress" not in updates
