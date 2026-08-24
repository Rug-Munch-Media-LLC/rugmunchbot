"""LP-expiry monitor tests (win 35)."""

import asyncio
from unittest.mock import patch

from rugmunchbot.tools.lp_expiry import check_lp_expiry


def _mock_response(status_code=200, body=None):
    class R:
        def __init__(self, sc=status_code, b=body or {}):
            self.status_code = sc
            self._body = b

        def json(self):
            return self._body

    return R()


async def _check(body):
    with patch("httpx.AsyncClient.get", return_value=_mock_response(200, body)):
        return await check_lp_expiry("0xABC")


def test_expiring_lock_flagged():
    body = {"lp_locked_pct": 95, "lock_days_remaining": 3}
    r = asyncio.run(_check(body))
    assert r["status"] == "EXPIRING"
    assert r["severity"] == "WARNING"


def test_critical_expiry():
    body = {"lp_locked_pct": 100, "lock_days_remaining": 1}
    r = asyncio.run(_check(body))
    assert r["severity"] == "CRITICAL"


def test_safe_lock_not_flagged():
    body = {"lp_locked_pct": 100, "lock_days_remaining": 365}
    r = asyncio.run(_check(body))
    assert r["status"] == "safe"


def test_no_lock_honest():
    body = {"lp_locked_pct": 0}
    r = asyncio.run(_check(body))
    assert r["status"] == "no_lock"
    assert "pulled anytime" in r["risk"]
