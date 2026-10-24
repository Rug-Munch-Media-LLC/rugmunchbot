"""Channel scan churn — failed scans are dropped, churned notes are flagged.

Split out of test_whale_alpha_routing.py (file-size policy: <=200 lines).
"""

from __future__ import annotations


def test_scan_churn_drops_candidate_when_scan_fails():
    """A candidate whose full scan fails is DROPPED, never posted from sketch
    flags alone — a sketch-only "unscanned · thin_liquidity" card carries no
    why (scans-of-interest rule: either run the deep scan so we HAVE data,
    or drop it)."""
    import asyncio

    from rugmunchbot.channels import content

    async def _failed_scan(address, chain):
        return None

    async def _timeout_scan(address, chain):
        raise TimeoutError("backend slow")

    import rugmunchbot.tools.scanner._scans as scanner_module

    original = scanner_module.scan_token_v2
    try:
        scanner_module.scan_token_v2 = _failed_scan
        n_fail = asyncio.run(
            content._churn_one_scan(
                {
                    "address": "SketchAddr11111111111111111111111111111111",
                    "symbol": "SUS",
                    "name": "Sus Token",
                    "chain": "solana",
                    "risk_flags": ["thin_liquidity", "fresh_launch", "unscanned"],
                    "sketch_score": 59.6,
                }
            )
        )
        scanner_module.scan_token_v2 = _timeout_scan
        n_timeout = asyncio.run(
            content._churn_one_scan(
                {
                    "address": "SketchAddr22222222222222222222222222222222",
                    "symbol": "SUS2",
                    "chain": "solana",
                    "risk_flags": ["thin_liquidity"],
                    "sketch_score": 61.0,
                }
            )
        )
    finally:
        scanner_module.scan_token_v2 = original
    assert n_fail is None
    assert n_timeout is None


def test_scan_churn_notes_are_flagged_churned(monkeypatch):
    """fetch_suspect_scan_notes returns notes marked churned=True."""
    from rugmunchbot.channels import content

    async def _fake_churn_one(candidate):
        return {"address": "X", "symbol": "S", "churned": True, "risk_flags": ["x"]}

    async def _fake_scanner():
        return [
            {
                "address": "X",
                "chain": "solana",
                "symbol": "S",
                "risk_score": 85.0,
                "is_honeypot": True,
            }
        ]

    monkeypatch.setattr(content, "_churn_one_scan", _fake_churn_one)
    # fetch_suspect_scan_notes imports _scanner_candidates locally from
    # rugmunchbot.tools.trend_algo._candidates — patch it at the module it imports from.
    import rugmunchbot.tools.trend_algo._candidates as trend_algo

    monkeypatch.setattr(trend_algo, "_scanner_candidates", _fake_scanner)
    import asyncio

    notes = asyncio.run(content.fetch_suspect_scan_notes(limit=2))
    assert notes, "suspect scan notes should be returned"
    assert notes[0]["churned"] is True
