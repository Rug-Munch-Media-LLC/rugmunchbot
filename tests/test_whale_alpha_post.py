"""Tests for whale-post alpha-signal rendering (_posts_whales._alpha_signal_lines)."""

from __future__ import annotations

from rugmunchbot.channels.scheduler._posts_whales import (
    _alpha_signal_lines,
    _fmt_doc,
)


class TestAlphaSignalLines:
    def test_nested_signal_renders_why_and_track(self):
        meta = {
            "alpha_signal": {
                "type": "exit_after_pump",
                "why_notable": "Selling XYZ after gains — wallet has banked $42,000",
                "track_record": {"round_trips": 6, "win_rate": 0.833, "realized_pnl_usd": 42000.0},
            }
        }
        lines = _alpha_signal_lines(meta)
        assert len(lines) == 2
        assert "ALPHA (exit after pump)" in lines[0]
        assert "Selling XYZ" in lines[0]
        assert "83%" in lines[1]
        assert "$42,000" in lines[1]
        assert "6 tracked round trips" in lines[1]

    def test_flat_fallback_keys(self):
        meta = {"alpha_type": "large_swap", "alpha_why": "10x wallet average size"}
        lines = _alpha_signal_lines(meta)
        assert len(lines) == 1
        assert "ALPHA (large swap)" in lines[0]
        assert "10x wallet average size" in lines[0]

    def test_no_signal_returns_empty(self):
        assert _alpha_signal_lines({}) == []
        assert _alpha_signal_lines({"alpha_signal": None}) == []

    def test_signal_without_track_record(self):
        meta = {"alpha_signal": {"type": "new_token_entry", "why_notable": "First XYZ position"}}
        lines = _alpha_signal_lines(meta)
        assert len(lines) == 1
        assert "First XYZ position" in lines[0]


class TestFmtDocWithAlpha:
    def test_doc_includes_alpha_lines(self):
        payload = {
            "metadata": {
                "chain": "ethereum",
                "type": "large_transfer",
                "from": "0xabc",
                "to": "0xdef",
                "amount_usd": 500_000,
                "token": "USDC",
                "published": "2026-08-31T12:00:00+00:00",
                "alpha_signal": {
                    "type": "large_swap",
                    "why_notable": "16x this wallet's average size",
                    "track_record": {
                        "round_trips": 9,
                        "win_rate": 0.9,
                        "realized_pnl_usd": 1_200_000,
                    },
                },
            },
            "content": "",
        }
        doc = _fmt_doc("p1", payload)
        assert doc is not None
        assert "ALPHA (large swap)" in doc
        assert "90% win rate" in doc
        assert "Full analysis" in doc

    def test_doc_without_wallet_still_dropped(self):
        payload = {
            "metadata": {"chain": "ethereum", "type": "large_transfer", "amount_usd": 5},
            "content": "",
        }
        assert _fmt_doc("p1", payload) is None
