"""Wave 40 — 🛒 First Buyers end-to-end contracts (2026-08-25 quality push).

Covers the bot side of the own-indexer first-buyers engine:

* Formatter — 🛒 First Buyers block: basic = aggregates line only;
  deep = top-10 buyer rows + aggregates; honest-data rule (no payload →
  no section, never fabricated).
* Flatten — ``first_buyers`` passes through ``v2_token_to_legacy``.
* Merge — ``_merge_completed_scan`` fills first_buyers from the completed
  status payload without ever overwriting fast-path data.
* Poll — ``_poll_v2_scan_completion`` bounded polling: complete → payload,
  running past deadline → None (fail-open rendering).
"""

from __future__ import annotations

from typing import Any

import pytest

from rugmunchbot.formatters._scan_report_main import (
    format_deep_scan_report,
    format_scan_report,
)
from rugmunchbot.handlers._scanning_helpers import _merge_completed_scan
from rugmunchbot.tools.scanner._flatten import v2_token_to_legacy

# ── fixtures ─────────────────────────────────────────────────────────


def _buyer(
    wallet: str, bucket: str = "fresh", bundled: bool = False, age_s: int | None = 45
) -> dict[str, Any]:
    return {
        "wallet": wallet,
        "first_buy_ts": "2026-08-25T12:00:00+00:00",
        "tokens": 1_000_000.0,
        "wallet_age_bucket": bucket,
        "age_at_buy_s": age_s,
        "bundled": bundled,
    }


def _first_buyers(n: int = 25) -> dict[str, Any]:
    wallets = [f"0x{i:040x}" for i in range(1, n + 1)]
    return {
        "buyers": [
            _buyer(w, bucket="fresh" if i % 2 == 0 else "established", bundled=i < 3)
            for i, w in enumerate(wallets, 1)
        ],
        "aggregates": {
            "unique_buyers": n,
            "fresh_wallet_pct": 50.0,
            "new_wallet_pct": 60.0,
            "established_pct": 40.0,
            "bundled_pct": 12.0,
            "median_latency_s": 45.0,
        },
        "limit": n,
        "chain": "ethereum",
    }


def _report(**extra: Any) -> dict[str, Any]:
    r: dict[str, Any] = {
        "name": "Pepe",
        "symbol": "PEPE",
        "chain": "ethereum",
        "address": "0x" + "ab" * 20,
        "risk_score": 85,
        "honeypot": False,
        "contract_verified": True,
        "mintable": False,
        "owner_renounced": True,
    }
    r.update(extra)
    return r


# ── formatter: basic card ────────────────────────────────────────────


def test_basic_card_renders_first_buyers_aggregates_line() -> None:
    out = format_scan_report(_report(first_buyers=_first_buyers()))
    assert "\U0001f6d2 <b>First Buyers</b>" in out
    # Aggregates line carries the counts that matter to a degen:
    assert "<b>25</b> tracked" in out
    assert "50% fresh wallets" in out
    assert "12% bundled" in out
    # Basic mode NEVER renders per-wallet rows (top-10 is deep-only).
    assert "<code>0x0000...0001</code>" not in out


def test_basic_card_first_buyers_absent_without_payload() -> None:
    """Honest-data rule: older backend / ClickHouse down → no section."""
    out = format_scan_report(_report())
    assert "First Buyers" not in out


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"buyers": []},
        {"buyers": "garbage"},
        {"aggregates": {"fresh_wallet_pct": 99.0}},  # no buyers list
    ],
    ids=["empty-dict", "empty-buyers", "garbage-buyers", "aggregates-only"],
)
def test_basic_card_first_buyers_garbage_payloads_render_nothing(payload: dict[str, Any]) -> None:
    out = format_scan_report(_report(first_buyers=payload))
    assert "First Buyers" not in out


# ── formatter: deep card ─────────────────────────────────────────────


def test_deep_card_renders_top10_rows_plus_aggregates() -> None:
    fb = _first_buyers(n=200)
    out = format_deep_scan_report(_report(first_buyers=fb))
    assert "\U0001f6d2 <b>First Buyers</b>" in out
    # Top-10 buyer rows render with short addresses…
    assert "<code>0x0000...0001</code>" in out
    assert "<code>0x0000...000a</code>" in out  # buyer #10
    assert "<code>0x0000...000b</code>" not in out  # buyer #11 must NOT render
    # …bucket labels, bundle markers and compact ages…
    assert "\U0001f195 fresh" in out
    assert "\U0001f3e6 est" in out
    assert "\U0001f517 bundle" in out
    assert "bought 45s old" in out
    # …and the aggregates line sits over the FULL tracked set (200).
    assert "<b>200</b> tracked" in out
    assert "entry wave +45s" in out


def test_deep_card_skips_malformed_buyer_rows() -> None:
    """Non-dict / empty-wallet entries are skipped, valid ones still render."""
    fb = _first_buyers(n=4)
    fb["buyers"][0] = "not-a-dict"
    fb["buyers"][1] = {"wallet": "", "wallet_age_bucket": "fresh"}
    out = format_deep_scan_report(_report(first_buyers=fb))
    assert "<code>0x0000...0003</code>" in out
    # Only the 2 valid buyers remain; numbering stays sequential.
    assert "  1. <code>0x0000...0003</code>" in out
    assert "  2. <code>0x0000...0004</code>" in out


# ── flatten passthrough ──────────────────────────────────────────────


def test_flatten_carries_first_buyers_through() -> None:
    fb = _first_buyers(n=3)
    legacy = v2_token_to_legacy({"first_buyers": fb})
    assert legacy["first_buyers"] == fb


def test_flatten_first_buyers_none_when_absent() -> None:
    legacy = v2_token_to_legacy({})
    assert legacy["first_buyers"] is None


# ── merge: completed status payload → fast-path result ──────────────


def test_merge_fills_first_buyers_when_fast_path_lacks_it() -> None:
    result_v2: dict[str, Any] = {"symbol": "TEST"}
    completed = {"first_buyers": _first_buyers(n=5)}
    _merge_completed_scan(result_v2, completed)
    assert result_v2["first_buyers"]["limit"] == 5


def test_merge_never_overwrites_existing_first_buyers() -> None:
    existing = _first_buyers(n=2)
    result_v2: dict[str, Any] = {"first_buyers": existing}
    _merge_completed_scan(result_v2, {"first_buyers": _first_buyers(n=9)})
    assert result_v2["first_buyers"] is existing


# ── poll: bounded completion wait ────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self._payloads = payloads
        self.calls = 0

    async def get(self, url: str, **_: Any) -> _FakeResponse:
        payload = self._payloads[min(self.calls, len(self._payloads) - 1)]
        self.calls += 1
        return _FakeResponse(payload)


async def test_poll_returns_payload_once_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        [
            {"background_status": "running"},
            {"background_status": "complete", "first_buyers": {"ok": 1}},
        ]
    )
    monkeypatch.setattr(
        "rugmunchbot.handlers._scan_handlers._scan.get_shared_client",
        lambda _t: client,
    )
    from rugmunchbot.handlers._scan_handlers._scan import _poll_v2_scan_completion

    out = await _poll_v2_scan_completion(
        "0xabc", "ethereum", max_wait_s=5, head_start_s=0, interval_s=0.01
    )
    assert out is not None
    assert out["background_status"] == "complete"


async def test_poll_times_out_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Status never completes → None within the bounded budget (fail-open)."""
    client = _FakeClient([{"background_status": "running"}])
    monkeypatch.setattr(
        "rugmunchbot.handlers._scan_handlers._scan.get_shared_client",
        lambda _t: client,
    )
    from rugmunchbot.handlers._scan_handlers._scan import _poll_v2_scan_completion

    out = await _poll_v2_scan_completion(
        "0xabc", "ethereum", max_wait_s=0.05, head_start_s=0, interval_s=0.02
    )
    assert out is None


async def test_poll_survives_client_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP failures during polling don't crash the wait — keep polling."""

    class _ExplodingClient:
        async def get(self, url: str, **_: Any) -> _FakeResponse:
            raise ConnectionError("backend hiccup")

    monkeypatch.setattr(
        "rugmunchbot.handlers._scan_handlers._scan.get_shared_client",
        lambda _t: _ExplodingClient(),
    )
    from rugmunchbot.handlers._scan_handlers._scan import _poll_v2_scan_completion

    out = await _poll_v2_scan_completion(
        "0xabc", "ethereum", max_wait_s=0.05, head_start_s=0, interval_s=0.02
    )
    assert out is None
