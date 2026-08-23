"""Cross-venue funding dislocation detector tests."""

from unittest.mock import patch

from rugmunchbot.tools import funding_dislocation as fd


def test_spread_detection_and_severity():
    rows = [
        {
            "asset": "TEST",
            "total_oi_usd": 5e6,
            "venues": {
                "A": {"oi_usd": 3e6, "funding": 0.003},
                "B": {"oi_usd": 2e6, "funding": 0.0001},
            },
        }
    ]
    with patch.object(fd.httpx.AsyncClient, "get") as mock_get:

        class R:
            status_code = 200

            def json(self):
                return {"heatmap": rows}

        mock_get.return_value = R()
        r = asyncio_run(fd.funding_dislocation(min_oi_usd=1e6))
    assert r.ok
    sigs = r.data["signals"]
    assert len(sigs) == 1
    assert sigs[0]["severity"] == "EXTREME"  # 0.0029 spread > 0.002
    assert sigs[0]["richest_funding"]["venue"] == "A"


def test_low_oi_filtered():
    rows = [
        {
            "asset": "DUST",
            "total_oi_usd": 5e4,
            "venues": {"A": {"oi_usd": 5e4, "funding": 0.01}},
        }
    ]
    with patch.object(fd.httpx.AsyncClient, "get") as mock_get:

        class R:
            status_code = 200

            def json(self):
                return {"heatmap": rows}

        mock_get.return_value = R()
        r = asyncio_run(fd.funding_dislocation(min_oi_usd=1e6))
    assert r.ok
    assert r.data["flagged"] == 0


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
