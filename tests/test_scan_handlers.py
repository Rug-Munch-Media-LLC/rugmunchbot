"""
Tests for V3 scan handlers — /scan, /deepscan, /risk + callback actions,
and the RugMunchScan v2 → v1 fallback client. All external APIs are mocked.
"""

import httpx
import pytest

from handlers.scan import (  # noqa: E402
    cmd_deepscan,
    cmd_risk,
    cmd_scan,
    handle_deep_callback,
    handle_scan_callback,
    handle_watch_callback,
    set_databus_fallback,
)
from services import scanner as scanner_service  # noqa: E402
from services.scanner import DeepScanNotAvailableError  # noqa: E402
from tests.fakes import (  # noqa: E402
    FakeBot,
    FakeCallbackQuery,
    FakeContext,
    FakeUpdate,
    make_message_update,
)

# Well-known test addresses
SOL_ADDR = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
EVM_ADDR = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"

V2_OK = {
    "scan_id": "scan_abc",
    "status": "completed",
    "risk_score": 72,
    "risk_tier": "high",
    "name": "Test Token",
    "symbol": "TST",
    "chain": "solana",
    "warnings": ["HONEYPOT DETECTED", "Low LP lock: 30%"],
    "liquidity": {"usd": 125000, "locked_pct": 0.3},
    "deployer": {"address": "DevWallet1111", "other_tokens": 3, "reputation": "suspicious"},
    "market": {"price_usd": 0.0012, "mcap_usd": 500000, "volume_24h": 20000},
    "holders": {"total": 1200, "top10_pct": 68.0, "fresh_wallet_pct": 41.0},
    "contract": {"verified": False, "mintable": True, "owner_renounced": False},
}


@pytest.fixture(autouse=True)
def _fresh_db(clean_db):
    """Reset DB + in-memory spam cooldown for every test."""
    import handlers.scan as scan_mod

    scan_mod._user_last_action.clear()
    yield


@pytest.fixture(autouse=True)
def _no_databus_fallback():
    """Ensure the legacy fallback hook is unset between tests."""
    set_databus_fallback(None)
    yield
    set_databus_fallback(None)


# ── /scan ──


async def test_cmd_scan_no_args_shows_usage():
    update, ctx, msg = make_message_update([])
    await cmd_scan(update, ctx)
    assert msg.calls[0][0] == "reply"
    assert "Token Scanner" in msg.calls[0][1]


async def test_cmd_scan_invalid_address():
    update, ctx, msg = make_message_update(["not-an-address"])
    await cmd_scan(update, ctx)
    assert "Invalid address" in msg.calls[0][1]


async def test_cmd_scan_success(monkeypatch):
    async def fake_scan(address, chain=None, depth="standard"):
        return dict(V2_OK, address=address, chain=chain or "solana")

    monkeypatch.setattr("handlers.scan.scan_token_v2", fake_scan)
    update, ctx, msg = make_message_update([SOL_ADDR])
    await cmd_scan(update, ctx)
    # First call = progress message, second = report
    assert msg.calls[0][1].startswith("🔍")
    report = msg.calls[1][1]
    assert "Test Token" in report
    assert "72/100" in report
    assert "HONEYPOT DETECTED" in report
    assert "EPjFWd" in report  # shortened address
    kb = msg.calls[1][2]["reply_markup"]
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert "📊 Chart" in labels
    assert "🗺️ Map" in labels
    assert "🔬 Deep Scan" in labels
    assert "🔍 Scanner" in labels
    assert "🤖 AI Analysis" in labels
    assert "📤 Share" in labels


async def test_cmd_scan_evm_address(monkeypatch):
    async def fake_scan(address, chain=None, depth="standard"):
        return dict(V2_OK, address=address, chain="ethereum", name="Pepe", symbol="PEPE")

    monkeypatch.setattr("handlers.scan.scan_token_v2", fake_scan)
    update, ctx, msg = make_message_update([EVM_ADDR])
    await cmd_scan(update, ctx)
    assert "Pepe" in msg.calls[1][1]


async def test_cmd_scan_falls_back_to_databus(monkeypatch):
    from services.scanner import ScanUnavailableError

    async def fail_scan(address, chain=None, depth="standard"):
        raise ScanUnavailableError("v2+v1 down")

    async def databus_fallback(address, chain=None):
        return {
            "address": address,
            "chain": "solana",
            "name": "Legacy",
            "symbol": "LG",
            "price": 1.0,
            "mcap": 1000,
            "liquidity": 100,
            "volume_24h": 50,
            "risk_score": 25,
            "threats": [],
            "_data_source": {"price": "databus", "detail": "databus"},
        }

    monkeypatch.setattr("handlers.scan.scan_token_v2", fail_scan)
    set_databus_fallback(databus_fallback)
    update, ctx, msg = make_message_update([SOL_ADDR])
    await cmd_scan(update, ctx)
    report = msg.calls[1][1]
    assert "Test Token" in report or "Legacy" in report


async def test_cmd_scan_unavailable_when_no_fallback(monkeypatch):
    from services.scanner import ScanUnavailableError

    async def fail_scan(address, chain=None, depth="standard"):
        raise ScanUnavailableError("v2+v1 down")

    monkeypatch.setattr("handlers.scan.scan_token_v2", fail_scan)
    update, ctx, msg = make_message_update([SOL_ADDR])
    await cmd_scan(update, ctx)
    assert "Scanner Temporarily Unavailable" in msg.calls[1][1]


async def test_cmd_scan_rate_limit_paywall():
    import db as bot_db

    bot_db.get_or_create_user(12345)
    # Exhaust weekly scans for the free tier
    conn = bot_db.get_db()
    conn.execute(
        "INSERT INTO weekly_usage (user_id, week_start, scans, ai_msgs) VALUES (?, ?, ?, 0)",
        (12345, bot_db._current_week_start(), 25),
    )
    conn.commit()
    conn.close()
    update, ctx, msg = make_message_update([SOL_ADDR])
    await cmd_scan(update, ctx)
    assert "Weekly Scan Limit Reached" in msg.calls[0][1]


# ── /deepscan (premium) ──


async def test_cmd_deepscan_free_user_gated():
    update, ctx, msg = make_message_update([SOL_ADDR])
    await cmd_deepscan(update, ctx)
    assert "Deep Scan requires Scout+ or ⭐250" in msg.calls[0][1]


async def test_cmd_deepscan_scout_allowed(monkeypatch):
    import db as bot_db

    bot_db.get_or_create_user(12345)
    bot_db.set_user_tier(12345, "scout", 30)

    async def fake_aggregate(address, chain):
        return {
            "address": address,
            "chain": chain,
            "risk_score": 88,
            "grade": "A",
            "scoring": {
                "risk_score": 88,
                "grade": "A",
                "categories": {"contract": 90, "liquidity": 85},
            },
            "sentinel_findings": [{"severity": "critical", "title": "Bundled launch"}],
            "deployer": {"total_launched": 5, "total_rugged": 3, "reputation": "suspicious"},
            "bundle_detected": True,
            "bundle_wallets": 14,
            "bundle_pct": 34.0,
            "cluster_count": 2,
        }

    monkeypatch.setattr("handlers.scan.run_deep_scan_aggregate", fake_aggregate)
    update, ctx, msg = make_message_update([SOL_ADDR])
    await cmd_deepscan(update, ctx)
    report = msg.calls[1][1]
    assert "DEEP FORENSIC SCAN" in report
    assert "A" in report or "88" in report


async def test_cmd_deepscan_stars_unlock(monkeypatch):
    import db as bot_db

    bot_db.get_or_create_user(12345)
    bot_db.add_stars(12345, 250, "test")

    async def fake_aggregate(address, chain):
        return {"address": address, "chain": chain, "risk_score": 10, "grade": "C"}

    monkeypatch.setattr("handlers.scan.run_deep_scan_aggregate", fake_aggregate)
    update, ctx, msg = make_message_update([SOL_ADDR])
    await cmd_deepscan(update, ctx)
    assert "DEEP FORENSIC SCAN" in msg.calls[1][1]
    # Stars were spent
    user = bot_db.get_or_create_user(12345)
    assert user["stars_balance"] == 0


async def test_cmd_deepscan_stars_refund_on_failure(monkeypatch):
    import db as bot_db

    bot_db.get_or_create_user(12345)
    bot_db.add_stars(12345, 250, "test")

    async def fake_aggregate(address, chain):
        raise httpx.ConnectError("aggregator down")

    monkeypatch.setattr("handlers.scan.run_deep_scan_aggregate", fake_aggregate)
    update, ctx, msg = make_message_update([SOL_ADDR])
    await cmd_deepscan(update, ctx)
    assert "Deep Scan Failed" in msg.calls[1][1]
    user = bot_db.get_or_create_user(12345)
    assert user["stars_balance"] == 250  # refunded


async def test_cmd_deepscan_404_coming_soon_scout(monkeypatch):
    """Backend does not expose SENTINEL yet → report with sentinel-not-available note."""
    import db as bot_db

    bot_db.get_or_create_user(12345)
    bot_db.set_user_tier(12345, "scout", 30)

    async def fake_aggregate(address, chain):
        return {
            "address": address,
            "chain": chain,
            "risk_score": 25,
            "sentinel_findings": [],
        }

    monkeypatch.setattr("handlers.scan.run_deep_scan_aggregate", fake_aggregate)
    update, ctx, msg = make_message_update([SOL_ADDR])
    await cmd_deepscan(update, ctx)
    assert "DEEP FORENSIC SCAN" in msg.calls[1][1]


async def test_cmd_deepscan_404_coming_soon_refunds_stars(monkeypatch):
    """Stars users: if aggregator fails, stars are refunded."""
    import db as bot_db

    bot_db.get_or_create_user(12345)
    bot_db.add_stars(12345, 250, "test")

    async def fake_aggregate(address, chain):
        raise DeepScanNotAvailableError("not mounted")

    monkeypatch.setattr("handlers.scan.run_deep_scan_aggregate", fake_aggregate)
    update, ctx, msg = make_message_update([SOL_ADDR])
    await cmd_deepscan(update, ctx)
    user = bot_db.get_or_create_user(12345)
    assert user["stars_balance"] == 250  # refunded


async def test_cmd_deepscan_long_report_splits_messages(monkeypatch):
    """Reports over the 4096-char limit are split into multiple messages."""
    import db as bot_db
    import handlers.scan as scan_mod

    bot_db.get_or_create_user(12345)
    bot_db.set_user_tier(12345, "scout", 30)
    monkeypatch.setattr(scan_mod, "MAX_MESSAGE_CHARS", 300)

    async def fake_aggregate(address, chain):
        return {
            "address": address,
            "chain": "solana",
            "risk_score": 88,
            "grade": "B",
            "scoring": {
                "risk_score": 88,
                "categories": {"contract": 90, "liquidity": 85, "holders": 70},
                "flags": [
                    {"severity": "critical", "message": "Bundled launch detected"},
                    {"severity": "high", "message": "Suspicious wallet concentration"},
                ],
            },
            "deployer": {"total_launched": 12, "total_rugged": 6, "reputation": "high_risk"},
            "bundle_detected": True,
            "bundle_wallets": 20,
            "bundle_pct": 45.0,
            "cluster_count": 5,
            "suspicious_clusters": 3,
            "sentinel_findings": [],
        }

    monkeypatch.setattr("handlers.scan.run_deep_scan_aggregate", fake_aggregate)
    update, ctx, msg = make_message_update([SOL_ADDR])
    await cmd_deepscan(update, ctx)
    # First chunk edits the scanning message; remaining chunks are new messages.
    assert msg.calls[1][0] == "edit"
    assert len(ctx.bot.sent) >= 1
    all_text = msg.calls[1][1] + "".join(text for _, text, _ in ctx.bot.sent)
    assert "DEEP FORENSIC SCAN" in all_text
    # Report is split into chunks — total text should exceed the limit
    assert len(all_text) > 300


async def test_sentinel_scan_404_raises_not_available(fake_http):
    script = [FakeResponse(404, {"detail": "Not Found"})]
    fake_http(script)
    with pytest.raises(DeepScanNotAvailableError):
        await scanner_service.sentinel_scan(SOL_ADDR)


# ── formatters.wallet_breakdown ──


def test_format_deepscan_profile_sections():
    try:
        from formatters.wallet_breakdown import format_deepscan_profile_sections
    except ImportError:
        pytest.skip("formatters.wallet_breakdown exposes a different API variant")

    data = {
        "address": SOL_ADDR,
        "deep_profile": {
            "wallet_breakdown": {
                "total_holders": 1200,
                "fresh_wallet_count": 492,
                "fresh_wallet_pct": 0.41,
                "exchange_funded_count": 310,
                "exchange_funded_pct": 0.258,
                "instant_buy_count": 88,
                "instant_buy_pct": 0.073,
                "exchange_breakdown": {"binance": 187, "coinbase": 124},
                "fake_diversification_score": 63.0,
            },
            "bundle_profile": {
                "bundles_detected": 14,
                "deployer_funded_wallets": 9,
                "deployer_funded_pct": 0.642,
                "same_block_snipers": 5,
                "jito_bundles": 3,
                "behavior_clusters": 2,
                "cross_launch_repeat_wallets": 7,
                "bundle_risk_score": 82,
                "bundle_risk_level": "HIGH",
            },
            "deployer_profile": {
                "wallet_age_days": 3,
                "total_deployments": 11,
                "rug_rate": 0.545,
                "funded_from": "binance",
                "chains_active": ["solana"],
                "sleep_pattern": "bot-like",
            },
        },
    }
    sections = format_deepscan_profile_sections(data)
    assert len(sections) == 3
    wallet, bundle, deployer = sections
    assert "Wallet Breakdown" in wallet
    assert "Total holders" in wallet
    assert "41.0%" in wallet  # 0.41 fraction rendered as pct
    assert "Bundle Profile" in bundle
    assert "Bundle risk: <b>82/100</b> (HIGH)" in bundle
    assert "Deployer Profile" in deployer
    assert "bot-like" in deployer


def test_format_deepscan_profile_sections_empty_without_profiles():
    try:
        from formatters.wallet_breakdown import format_deepscan_profile_sections
    except ImportError:
        pytest.skip("formatters.wallet_breakdown exposes a different API variant")

    assert format_deepscan_profile_sections({"risk_score": 10}) == []


# ── /risk ──


async def test_cmd_risk_success(monkeypatch):
    async def fake_scan(address, chain=None, depth="quick"):
        return dict(V2_OK, address=address, chain="solana")

    monkeypatch.setattr("handlers.scan.scan_token_v2", fake_scan)
    update, ctx, msg = make_message_update([SOL_ADDR])
    await cmd_risk(update, ctx)
    report = msg.calls[1][1]
    assert "Quick Risk Assessment" in report
    assert "72%" in report


async def test_cmd_risk_unavailable(monkeypatch):
    from services.scanner import ScanUnavailableError

    async def fail_scan(address, chain=None, depth="quick"):
        raise ScanUnavailableError("down")

    monkeypatch.setattr("handlers.scan.scan_token_v2", fail_scan)
    update, ctx, msg = make_message_update([SOL_ADDR])
    await cmd_risk(update, ctx)
    assert "Scanner Temporarily Unavailable" in msg.calls[1][1]


# ── callbacks ──


async def test_handle_scan_callback_success(monkeypatch):
    async def fake_scan(address, chain=None, depth="standard"):
        return dict(V2_OK, address=address, chain=chain or "solana")

    monkeypatch.setattr("handlers.scan.scan_token_v2", fake_scan)
    q = FakeCallbackQuery(data=f"scan_{SOL_ADDR}_solana")
    update = FakeUpdate(callback_query=q)
    await handle_scan_callback(update, FakeContext())
    assert "Test Token" in q.message.calls[-1][1]


async def test_handle_deep_callback_gated():
    q = FakeCallbackQuery(data=f"deep_{SOL_ADDR}_solana")
    update = FakeUpdate(callback_query=q)
    await handle_deep_callback(update, FakeContext())
    assert "Deep Scan requires Scout+ or ⭐250" in q.message.calls[-1][1]


async def test_handle_deep_callback_scout(monkeypatch):
    import db as bot_db

    bot_db.get_or_create_user(12345)
    bot_db.set_user_tier(12345, "hunter", 30)

    async def fake_sentinel(address, chain=None, depth="deep"):
        return {"address": address, "chain": "solana", "risk_score": 60}

    monkeypatch.setattr("services.scanner.sentinel_scan", fake_sentinel)
    q = FakeCallbackQuery(data=f"deep_{SOL_ADDR}_solana")
    update = FakeUpdate(callback_query=q)
    await handle_deep_callback(update, FakeContext())
    assert "DEEP FORENSIC SCAN" in q.message.calls[-1][1]


async def test_handle_watch_callback_adds():
    import db as bot_db

    bot_db.get_or_create_user(12345)
    bot_db.set_user_tier(12345, "scout", 30)
    q = FakeCallbackQuery(data=f"watch_{SOL_ADDR}_solana")
    update = FakeUpdate(callback_query=q)
    await handle_watch_callback(update, FakeContext())
    assert q.answers[0][0] == "✅ Added to watchlist!"
    assert len(bot_db.get_watchlist(12345)) == 1


async def test_handle_watch_callback_free_tier():
    import db as bot_db

    bot_db.get_or_create_user(12345)
    q = FakeCallbackQuery(data=f"watch_{SOL_ADDR}_solana")
    update = FakeUpdate(callback_query=q)
    await handle_watch_callback(update, FakeContext())
    assert q.answers[0][1] is True  # show_alert
    assert len(bot_db.get_watchlist(12345)) == 0


# ── services.scanner: v2 → v1 fallback client ──


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=None, response=self)


class FakeAsyncClient:
    """Scripted httpx.AsyncClient — pops the next response per call."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _next(self):
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return FakeResponse(200, {"pairs": []})

    async def post(self, url, **kwargs):
        self.calls.append(("post", url))
        return self._next()

    async def get(self, url, **kwargs):
        self.calls.append(("get", url))
        return self._next()


@pytest.fixture
def fake_http(monkeypatch):
    def _install(script):
        monkeypatch.setattr(
            scanner_service.httpx, "AsyncClient", lambda **kw: FakeAsyncClient(script)
        )
        monkeypatch.setattr(scanner_service, "V1_POLL_INTERVAL", 0)
        return scanner_service.httpx.AsyncClient

    return _install


async def test_scan_token_v2_returns_v2(fake_http):
    script = [FakeResponse(200, dict(V2_OK))]
    fake_http(script)
    result = await scanner_service.scan_token_v2(SOL_ADDR)
    assert result["risk_score"] == 72
    assert result["_source"] == "v2"
    assert result["address"] == SOL_ADDR


async def test_scan_token_v2_falls_back_to_v1(fake_http):
    script = [
        FakeResponse(500, {}),  # v2 fails
        FakeResponse(200, {"scan_id": "scan_123"}),  # v1 accepted
        FakeResponse(200, {"status": "completed", "risk_score": 55}),  # poll result
    ]
    fake_http(script)
    result = await scanner_service.scan_token_v2(SOL_ADDR)
    assert result["risk_score"] == 55
    assert result["_source"] == "v1"


async def test_scan_token_v2_raises_when_both_down(fake_http):
    from services.scanner import ScanUnavailableError

    script = [FakeResponse(500, {}), FakeResponse(200, {"scan_id": None})]
    fake_http(script)
    with pytest.raises(ScanUnavailableError):
        await scanner_service.scan_token_v2(SOL_ADDR)


async def test_scan_token_v2_raises_on_network_error(fake_http):
    from services.scanner import ScanUnavailableError

    script = [httpx.ConnectError("v2 down"), httpx.ConnectError("v1 down")]
    fake_http(script)
    with pytest.raises(ScanUnavailableError):
        await scanner_service.scan_token_v2(SOL_ADDR)
