"""Per-channel content charter tests (2026-10-17).

Enforces the ONE authoritative channel contract and guards against the
double-config defect returning: there must be exactly ONE
``CHANNEL_CONTENT_TYPES`` and ONE ``channel_accepts_content``, defined in
``channels/content_charter.py`` and re-exported (never re-declared) elsewhere.

Covers:
1. The charter covers all 7 channels we run, each with purpose/types/cadence.
2. Every chartered content type is in the canonical vocabulary.
3. The gate blocks off-charter content and fails soft on unknown input.
4. Every chartered type resolves to a real brand graphic (operator's images).
5. The duplicate cannot return — no second definition anywhere in the tree.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from rugmunchbot.channels.content_charter import (
    CANONICAL_CONTENT_TYPES,
    CHANNEL_CHARTER,
    CHANNEL_CONTENT_TYPES,
    channel_accepts_content,
    charter_for,
)

#: The 7 channels we run (role → public handle).
EXPECTED_CHANNELS = {
    "main": "@cryptorugmuncher",
    "news": "@rmicryptonews",
    "alerts": "@rmialerts",
    "alpha": "@rmialpha",
    "scans": "@rmiscans",
    "updates": "@rmiupdates",
    "shills": "@rmishills",
}

#: The operator's original brand images the charter must wire to.
OPERATOR_GRAPHICS = {
    "rmi-scam-alert.jpeg",
    "rmi-breaking-news.jpeg",
    "rmi-whale-alert.jpeg",
    "rmi-evening-market-wrap.jpeg",
    "daily-rug-logo.jpeg",
    "current_cryptorugmuncher.png",
}


# ── 1. charter completeness ─────────────────────────────────────────────


def test_charter_covers_every_channel_we_run():
    assert set(CHANNEL_CHARTER) == set(EXPECTED_CHANNELS)


@pytest.mark.parametrize(("role", "handle"), sorted(EXPECTED_CHANNELS.items()))
def test_charter_row_is_complete(role, handle):
    charter = CHANNEL_CHARTER[role]
    assert charter.role == role
    assert charter.handle == handle
    assert len(charter.purpose) > 40, f"{role} purpose too thin to be a contract"
    assert charter.content_types, f"{role} accepts nothing"
    assert charter.cadence, f"{role} has no cadence"


def test_charter_for_resolves_and_is_case_insensitive():
    assert charter_for("MAIN") is CHANNEL_CHARTER["main"]
    assert charter_for("") is None
    assert charter_for("unknown") is None


# ── 2. vocabulary is canonical ──────────────────────────────────────────


def test_every_chartered_type_is_canonical():
    for role, charter in CHANNEL_CHARTER.items():
        unknown = charter.content_types - CANONICAL_CONTENT_TYPES
        assert not unknown, f"{role} charters non-canonical types: {unknown}"


def test_content_types_dict_derives_from_charter():
    """The gate dict must be the charter's own view — never a second copy."""
    for role, charter in CHANNEL_CHARTER.items():
        assert CHANNEL_CONTENT_TYPES[role] == charter.content_types


# ── 3. the gate ─────────────────────────────────────────────────────────


def test_gate_accepts_chartered_content():
    assert channel_accepts_content("main", "market")
    assert channel_accepts_content("news", "news")
    assert channel_accepts_content("alerts", "alert")
    assert channel_accepts_content("alerts", "scam_alert")
    assert channel_accepts_content("alpha", "whale")
    assert channel_accepts_content("scans", "scan")
    assert channel_accepts_content("updates", "update")
    assert channel_accepts_content("shills", "shill")


def test_gate_blocks_off_charter_content():
    """No channel posts off-charter content — the cross-contamination guard."""
    assert not channel_accepts_content("scans", "whale")
    assert not channel_accepts_content("alpha", "news")
    assert not channel_accepts_content("alerts", "market")
    assert not channel_accepts_content("news", "alert")
    assert not channel_accepts_content("updates", "news")
    assert not channel_accepts_content("shills", "scan")


def test_gate_fails_soft_on_unknown_input():
    """A mislabeled caller must never silently drop a post."""
    assert channel_accepts_content("", "market")
    assert channel_accepts_content("unknown-channel", "market")
    assert channel_accepts_content("main", "")
    assert channel_accepts_content("MAIN", "MARKET")  # case-insensitive


# ── 4. graphics wired to the operator's images ──────────────────────────


def test_every_chartered_type_resolves_to_a_graphic():
    for role, charter in CHANNEL_CHARTER.items():
        for ctype in charter.content_types:
            url = charter.graphic_for(ctype)
            assert url.startswith("https://"), f"{role}/{ctype} → {url!r}"


def test_charter_wires_the_operator_graphics():
    """The charter must reach the operator's original brand images."""
    wired = {
        CHANNEL_CHARTER["alerts"].graphic_for("scam_alert"),
        CHANNEL_CHARTER["news"].graphic_for("news"),
        CHANNEL_CHARTER["alpha"].graphic_for("whale"),
        CHANNEL_CHARTER["main"].graphic_for("market"),
    }
    for url in wired:
        assert any(asset in url for asset in OPERATOR_GRAPHICS), url


# ── 5. the duplicate cannot return ──────────────────────────────────────


def _defines_channel_content_types(path: Path) -> bool:
    """True when *path* assigns a module-level ``CHANNEL_CONTENT_TYPES``."""
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Name) and t.id == "CHANNEL_CONTENT_TYPES":
                return True
    return False


def _defines_channel_accepts_content(path: Path) -> bool:
    """True when *path* defines a module-level ``channel_accepts_content``."""
    tree = ast.parse(path.read_text(), filename=str(path))
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "channel_accepts_content"
        for node in tree.body
    )


def test_channel_content_types_defined_exactly_once():
    """The double-config defect: exactly ONE definition in the whole tree."""
    src = Path(__file__).resolve().parents[1] / "src"
    definers = [
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if _defines_channel_content_types(p)
    ]
    assert definers == ["rugmunchbot/channels/content_charter.py"], (
        f"CHANNEL_CONTENT_TYPES must be defined once, found in: {definers}"
    )


def test_channel_accepts_content_defined_exactly_once():
    """The double-config defect: exactly ONE gate function in the whole tree."""
    src = Path(__file__).resolve().parents[1] / "src"
    definers = [
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if _defines_channel_accepts_content(p)
    ]
    assert definers == ["rugmunchbot/channels/content_charter.py"], (
        f"channel_accepts_content must be defined once, found in: {definers}"
    )


def test_style_guides_reexports_the_single_source():
    """style_guides re-exports the charter objects — same identity, no copy."""
    from rugmunchbot.channels import style_guides

    assert style_guides.CHANNEL_CONTENT_TYPES is CHANNEL_CONTENT_TYPES
    assert style_guides.channel_accepts_content is channel_accepts_content
