"""Portfolio risk auditor tests."""

from unittest.mock import AsyncMock, patch

from rugmunchbot.tools.portfolio_risk import audit_portfolio


def _mock_vi(verdict, score):
    return {"verdict": verdict, "score": score, "pair": "TEST/WETH",
            "liquidity_usd": 500_000}


async def _mock_scan(addr):
    if "clean" in addr:
        return _mock_vi("CLEAN", 90)
    if "washed" in addr:
        return _mock_vi("LIKELY_WASHED", 30)
    return _mock_vi("SUSPECT", 55)


def test_grading():
    with patch("rugmunchbot.tools.portfolio_risk._scan_one", side_effect=_mock_scan):
        import asyncio
        from rugmunchbot.tools.portfolio_risk import audit_portfolio

        # 2 clean + 1 washed = 33% risky → grade B or C
        r = asyncio.run(audit_portfolio(["clean1", "clean2", "washed1"]))
        assert r["grade"] in ("A", "B", "C")
        assert r["risky_count"] == 1


def test_all_washed_critical():
    with patch("rugmunchbot.tools.portfolio_risk._scan_one", side_effect=_mock_scan):
        import asyncio
        from rugmunchbot.tools.portfolio_risk import audit_portfolio

        r = asyncio.run(audit_portfolio(["washed1", "washed2", "washed3"]))
        assert r["grade"] == "F"
        assert r["concentration_warning"] is not None


def test_empty_addresses():
    import asyncio
    from rugmunchbot.tools.portfolio_risk import audit_portfolio

    r = asyncio.run(audit_portfolio([]))
    assert "error" in r
