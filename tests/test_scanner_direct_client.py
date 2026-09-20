"""Offline tests for the direct scanner-service client (cutover batch-4).

Transport is mocked with ``respx`` (no live network, no code patching);
env config is set via ``os.environ`` and restored by an autouse fixture. Covers: flag on/off,
URL precedence, health gate, healthy scan round-trip, unreachable/not-ready
fallback, and error mapping (non-200 / non-``ok`` / bad JSON).
"""

from __future__ import annotations

import os

import httpx
import pytest
import respx

from rugmunchbot.tools.scanner import _scanner_direct as direct

BASE = "http://scanner.test"
_OK = httpx.Response(200, json={"status": "ok", "safety_score": 87})
_ENV_KEYS = (
    direct.SCANNER_DIRECT_ENV,
    direct.SCANNER_SERVICE_URL_ENV,
    direct.SCANNER_DIRECT_URL_ENV,
)


def _setenv(name: str, value: str) -> None:
    """Set an env var (DI stand-in for pytest's setenv helper)."""
    os.environ[name] = value


def _delenv(name: str) -> None:
    """Unset an env var (DI stand-in for pytest's delenv helper)."""
    os.environ.pop(name, None)


@pytest.fixture(autouse=True)
def _restore_env():
    """Snapshot the scanner env keys and restore them after each test."""
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture
def enabled_service():
    """Flag on, service URL set."""
    _setenv(direct.SCANNER_DIRECT_ENV, "1")
    _setenv(direct.SCANNER_SERVICE_URL_ENV, BASE)
    _delenv(direct.SCANNER_DIRECT_URL_ENV)
    return BASE


# ── flag ────────────────────────────────────────────────────────────────


def test_flag_disabled_by_default():
    _delenv(direct.SCANNER_DIRECT_ENV)
    assert direct.scanner_direct_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_flag_truthy(value):
    _setenv(direct.SCANNER_DIRECT_ENV, value)
    assert direct.scanner_direct_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_flag_falsy(value):
    _setenv(direct.SCANNER_DIRECT_ENV, value)
    assert direct.scanner_direct_enabled() is False


# ── url resolution ──────────────────────────────────────────────────────


def test_url_service_var_wins():
    _setenv(direct.SCANNER_SERVICE_URL_ENV, BASE + "/")
    _setenv(direct.SCANNER_DIRECT_URL_ENV, "http://legacy.test")
    assert direct.scanner_direct_url() == BASE  # trailing slash trimmed


def test_url_legacy_alias_accepted():
    _delenv(direct.SCANNER_SERVICE_URL_ENV)
    _setenv(direct.SCANNER_DIRECT_URL_ENV, "http://legacy.test")
    assert direct.scanner_direct_url() == "http://legacy.test"


def test_url_unset_is_none():
    _delenv(direct.SCANNER_SERVICE_URL_ENV)
    _delenv(direct.SCANNER_DIRECT_URL_ENV)
    assert direct.scanner_direct_url() is None


# ── inert paths (no HTTP) ───────────────────────────────────────────────


async def test_flag_off_makes_no_request():
    _delenv(direct.SCANNER_DIRECT_ENV)
    with respx.mock(assert_all_called=False) as mock:
        assert await direct.direct_scan_token("0xabc", "ethereum") is None
        assert await direct.direct_scan_wallet("0xabc", "ethereum") is None
        assert mock.calls.call_count == 0


async def test_flag_on_without_url_makes_no_request():
    _setenv(direct.SCANNER_DIRECT_ENV, "1")
    _delenv(direct.SCANNER_SERVICE_URL_ENV)
    _delenv(direct.SCANNER_DIRECT_URL_ENV)
    with respx.mock(assert_all_called=False) as mock:
        assert await direct.direct_scan_token("0xabc", "ethereum") is None
        assert mock.calls.call_count == 0


async def test_service_ready_requires_both_probes(enabled_service):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE}/healthz").mock(return_value=httpx.Response(200, json={}))
        mock.get(f"{BASE}/readyz").mock(return_value=httpx.Response(200, json={}))
        assert await direct.scanner_service_ready() is True


async def test_service_not_ready_when_readiness_503(enabled_service):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE}/healthz").mock(return_value=httpx.Response(200, json={}))
        mock.get(f"{BASE}/readyz").mock(return_value=httpx.Response(503, json={}))
        assert await direct.scanner_service_ready() is False


async def test_service_unreachable_is_false(enabled_service):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE}/healthz").mock(side_effect=httpx.ConnectError("refused"))
        assert await direct.scanner_service_ready() is False


# ── healthy scan round-trip ─────────────────────────────────────────────


async def test_direct_scan_token_happy_path(enabled_service):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE}/readyz").mock(return_value=httpx.Response(200, json={}))
        scan = mock.post(f"{BASE}{direct.V2_SCAN_PATH}").mock(return_value=_OK)
        data = await direct.direct_scan_token("0xabc", "ethereum")
    assert data is not None
    assert data["safety_score"] == 87
    sent = scan.calls.last.request
    assert sent.headers["content-type"] == "application/json"


async def test_direct_scan_token_deep_adds_depth_param(enabled_service):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE}/readyz").mock(return_value=httpx.Response(200, json={}))
        # The route only matches when depth=deep is present — a wrong payload
        # would leave the request unmatched and the call fail open.
        mock.post(f"{BASE}{direct.V2_SCAN_PATH}", params={"depth": "deep"}).mock(return_value=_OK)
        data = await direct.direct_scan_token("0xabc", "ethereum", deep=True)
    assert data is not None
    assert data["safety_score"] == 87


async def test_direct_scan_wallet_happy_path(enabled_service):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE}/readyz").mock(return_value=httpx.Response(200, json={}))
        mock.post(f"{BASE}{direct.V2_WALLET_SCAN_PATH}").mock(return_value=_OK)
        data = await direct.direct_scan_wallet("0xabc", "ethereum")
    assert data is not None
    assert data["safety_score"] == 87


# ── fallback + error mapping ────────────────────────────────────────────


async def test_unready_gate_skips_scan(enabled_service):
    """Not-ready service → fail open WITHOUT hitting the scan route."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE}/readyz").mock(return_value=httpx.Response(503, json={}))
        scan = mock.post(f"{BASE}{direct.V2_SCAN_PATH}")
        assert await direct.direct_scan_token("0xabc", "ethereum") is None
        assert not scan.called


async def test_scan_connection_error_fails_open(enabled_service):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE}/readyz").mock(return_value=httpx.Response(200, json={}))
        mock.post(f"{BASE}{direct.V2_SCAN_PATH}").mock(side_effect=httpx.ConnectError("boom"))
        assert await direct.direct_scan_token("0xabc", "ethereum") is None


async def test_scan_non_200_fails_open(enabled_service):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE}/readyz").mock(return_value=httpx.Response(200, json={}))
        mock.post(f"{BASE}{direct.V2_SCAN_PATH}").mock(
            return_value=httpx.Response(500, json={"detail": "boom"})
        )
        assert await direct.direct_scan_token("0xabc", "ethereum") is None


async def test_scan_non_ok_status_fails_open(enabled_service):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE}/readyz").mock(return_value=httpx.Response(200, json={}))
        mock.post(f"{BASE}{direct.V2_SCAN_PATH}").mock(
            return_value=httpx.Response(200, json={"status": "error"})
        )
        assert await direct.direct_scan_token("0xabc", "ethereum") is None


async def test_scan_bad_json_fails_open(enabled_service):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE}/readyz").mock(return_value=httpx.Response(200, json={}))
        mock.post(f"{BASE}{direct.V2_SCAN_PATH}").mock(
            return_value=httpx.Response(200, content=b"not-json")
        )
        assert await direct.direct_scan_token("0xabc", "ethereum") is None


async def test_scan_404_route_absent_fails_open(enabled_service):
    """The standalone runtime 404s scan routes until batch-3 — must fall back."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE}/readyz").mock(return_value=httpx.Response(200, json={}))
        mock.post(f"{BASE}{direct.V2_SCAN_PATH}").mock(
            return_value=httpx.Response(404, json={"status": "not_found"})
        )
        assert await direct.direct_scan_token("0xabc", "ethereum") is None
