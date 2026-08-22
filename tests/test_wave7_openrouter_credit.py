"""Wave 7 — openrouter_credit: formatting + credit fetch (fail-open contract).

Behavior probed against the implementation before asserting (oracle policy):
_fmt_usd renders 'unlimited' for None/garbage, thousands-separated otherwise;
format_credit_summary has distinct error / no-data / unlimited / capped shapes;
fetch_openrouter_credit never returns or logs the key and fails open with
structured error codes.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from rugmunchbot.openrouter_credit import (
    OPENROUTER_KEY_ENDPOINT,
    _fmt_usd,
    fetch_openrouter_credit,
    format_credit_summary,
)

# ── _fmt_usd ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "unlimited"),
        ("bad", "unlimited"),
        (0, "$0.00"),
        (12.5, "$12.50"),
        (1234.567, "$1,234.57"),
    ],
)
def test_fmt_usd(value: object, expected: str) -> None:
    assert _fmt_usd(value) == expected  # type: ignore[arg-type]


# ── format_credit_summary ───────────────────────────────────────────────


def test_summary_error_key_hint() -> None:
    out = format_credit_summary({"ok": False, "error": "key_not_configured"})
    assert "key_not_configured" in out
    assert "OPENROUTER_BACKUP_API_KEY" in out


def test_summary_error_no_hint_for_http() -> None:
    out = format_credit_summary({"ok": False, "error": "http_401"})
    assert "http_401" in out
    assert "OPENROUTER_BACKUP_API_KEY" not in out


def test_summary_no_usage_data() -> None:
    assert format_credit_summary(
        {"ok": True, "usage": None, "limit": None, "limit_remaining": None}
    ) == ("\U0001f4b3 OpenRouter: <b>no usage data</b>")


def test_summary_free_tier_unlimited() -> None:
    out = format_credit_summary(
        {"ok": True, "usage": 3.2, "limit": None, "limit_remaining": None, "is_free_tier": True}
    )
    assert "(free tier)" in out
    assert "$3.20" in out
    assert "unlimited" in out


def test_summary_capped_with_remaining() -> None:
    out = format_credit_summary({"ok": True, "usage": 1.5, "limit": 10.0, "limit_remaining": 8.5})
    assert "$8.50</b> / $10.00" in out


def test_summary_never_renders_a_key() -> None:
    out = format_credit_summary({"ok": True, "usage": 1.0, "limit": 2.0, "limit_remaining": 1.0})
    assert "sk-" not in out
    assert "Bearer" not in out


# ── fetch_openrouter_credit ─────────────────────────────────────────────


async def test_fetch_missing_key_fails_closed_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rugmunchbot.llm_config.OPENROUTER_BACKUP_API_KEY", "")
    out = await fetch_openrouter_credit()
    assert out == {"ok": False, "error": "key_not_configured"}


@respx.mock
async def test_fetch_ok_derives_remaining(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rugmunchbot.llm_config.OPENROUTER_BACKUP_API_KEY", "test-key")
    route = respx.get(OPENROUTER_KEY_ENDPOINT).mock(
        return_value=httpx.Response(
            200, json={"data": {"usage": 3.0, "limit": 10.0, "is_free_tier": False}}
        )
    )
    out = await fetch_openrouter_credit()
    assert route.called
    assert out["ok"] is True
    assert out["usage"] == 3.0
    assert out["limit"] == 10.0
    # limit_remaining absent upstream → derived as max(0, limit - usage)
    assert out["limit_remaining"] == 7.0
    assert out["is_free_tier"] is False
    assert out["error"] is None


@respx.mock
async def test_fetch_uncapped_key_keeps_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rugmunchbot.llm_config.OPENROUTER_BACKUP_API_KEY", "test-key")
    respx.get(OPENROUTER_KEY_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"data": {"usage": 1.0, "limit": None}})
    )
    out = await fetch_openrouter_credit()
    assert out["ok"] is True
    assert out["limit"] is None
    assert out["limit_remaining"] is None


@respx.mock
async def test_fetch_http_error_code_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rugmunchbot.llm_config.OPENROUTER_BACKUP_API_KEY", "test-key")
    respx.get(OPENROUTER_KEY_ENDPOINT).mock(return_value=httpx.Response(401))
    out = await fetch_openrouter_credit()
    assert out == {"ok": False, "error": "http_401"}


@respx.mock
async def test_fetch_bad_json_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rugmunchbot.llm_config.OPENROUTER_BACKUP_API_KEY", "test-key")
    respx.get(OPENROUTER_KEY_ENDPOINT).mock(return_value=httpx.Response(200, text="not-json"))
    out = await fetch_openrouter_credit()
    assert out == {"ok": False, "error": "bad_json"}


@respx.mock
async def test_fetch_network_error_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rugmunchbot.llm_config.OPENROUTER_BACKUP_API_KEY", "test-key")
    respx.get(OPENROUTER_KEY_ENDPOINT).mock(side_effect=httpx.ConnectError("boom"))
    out = await fetch_openrouter_credit()
    assert out == {"ok": False, "error": "network"}
