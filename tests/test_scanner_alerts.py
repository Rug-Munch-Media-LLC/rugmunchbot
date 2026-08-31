"""Scanner-alert rail tests — formatter, fetch fail-open, delivery, dedupe.

Covers the 2026-08-31 pipeline wiring: HTML formatting per the alerts
channel contract, fail-open fetch (unreachable RugBrain lane → no-op),
durable dedupe via the scheduler's global ``_remember`` namespace, and the
fail-open rule that a failed send releases its mark (retries next tick).
Network-free: the bot and channel resolver are injected.
"""

from __future__ import annotations

from typing import Any

import pytest

import rugmunchbot.channels.scanner_alerts as sa
import rugmunchbot.channels.scheduler._core as core


def _alert(aid: str = "scanalert-1", sev: str = "high") -> dict[str, Any]:
    return {
        "id": aid,
        "title": "Token flagged high risk on Base",
        "severity": sev,
        "name": "Moonwell",
        "symbol": "WELL",
        "chain": "Base",
        "safety_score": 78,
        "risk_flags": ["honeypot", "concentrated_holders", "low_liquidity"],
        "url": "https://rugmunch.io/scanner?token=0xabc&chain=base",
        "image_url": "https://example.com/chart.png",
        "detected_at": "2026-08-31T03:00:00Z",
        "status": "active",
    }


class _RecorderBot:
    """Records sends; can be told to fail."""

    def __init__(self, succeed: bool = True):
        self.messages: list[tuple[str, str]] = []
        self.photos: list[tuple[str, str, str]] = []
        self.succeed = succeed

    async def send_message(self, **kw: Any) -> Any:
        if not self.succeed:
            raise RuntimeError("boom")
        self.messages.append((kw.get("chat_id"), kw.get("text")))
        return True

    async def send_photo(self, **kw: Any) -> Any:
        if not self.succeed:
            raise RuntimeError("boom")
        self.photos.append((kw.get("chat_id"), kw.get("photo"), kw.get("caption")))
        return True


@pytest.fixture
def isolate(monkeypatch):
    """Stub the channel resolver + reset the global dedupe set."""
    monkeypatch.setattr(
        core, "_resolve_channel_id", lambda ch: "-100alerts" if ch == "alerts" else None
    )
    core._posted_keys.setdefault("__global__", set()).clear()
    return True


class TestFormat:
    def test_format_contains_required_parts(self) -> None:
        text = sa.format_scanner_alert(_alert())
        assert "Moonwell" in text
        assert "Base" in text
        assert "Risk Score: 78%" in text
        assert "honeypot" in text
        assert "rugmunch.io/scanner" in text

    def test_format_escapes_html(self) -> None:
        alert = _alert()
        alert["name"] = "<script>x</script>"
        text = sa.format_scanner_alert(alert)
        assert "<script>" not in text
        assert "&lt;script&gt;" in text

    def test_link_appears_once(self) -> None:
        text = sa.format_scanner_alert(_alert())
        # one link line: href + display text, no duplicates
        assert text.count('href="https://rugmunch.io') == 1


class TestFetch:
    async def test_unreachable_lane_fails_open(self, monkeypatch) -> None:
        class _Boom:
            def __init__(self, *a: Any, **k: Any) -> None:
                pass

            async def __aenter__(self) -> Any:
                raise OSError("refused")

            async def __aexit__(self, *a: Any) -> bool:
                return False

        monkeypatch.setattr(sa.httpx, "AsyncClient", _Boom)
        assert await sa.fetch_rag_alerts() == []


class TestDelivery:
    async def test_new_alert_posts(self, monkeypatch, isolate) -> None:
        async def _fetch(minutes: int | None = None) -> list[dict[str, Any]]:
            return [_alert()]

        monkeypatch.setattr(sa, "fetch_rag_alerts", _fetch)
        bot = _RecorderBot()
        stats = await sa.run_scanner_alert_watch(bot)
        assert stats["posted"] == 1
        assert bot.messages[0][0] == "-100alerts"

    async def test_skips_duplicate_incident(self, monkeypatch, isolate) -> None:
        """Second sweep with the same alert id is deduped (no re-post)."""
        store: list[dict[str, Any]] = [_alert("dup-1")]

        async def _fetch(minutes: int | None = None) -> list[dict[str, Any]]:
            return list(store)

        monkeypatch.setattr(sa, "fetch_rag_alerts", _fetch)
        bot = _RecorderBot()
        stats1 = await sa.run_scanner_alert_watch(bot)
        stats2 = await sa.run_scanner_alert_watch(bot)
        assert stats1["posted"] == 1
        assert stats2["posted"] == 0
        assert len(bot.messages) == 1

    async def test_failed_send_releases_mark(self, monkeypatch) -> None:
        async def _fetch(minutes: int | None = None) -> list[dict[str, Any]]:
            return [_alert("retry-1")]

        monkeypatch.setattr(sa, "fetch_rag_alerts", _fetch)
        bot = _RecorderBot(succeed=False)
        stats = await sa.run_scanner_alert_watch(bot)
        assert stats["failed"] == 1
        # mark released → next tick retries the same incident
        stats2 = await sa.run_scanner_alert_watch(bot)
        assert stats2["seen"] == 1

    async def test_missing_channel_skips(self, monkeypatch) -> None:
        monkeypatch.setattr(core, "_resolve_channel_id", lambda ch: None)
        bot = _RecorderBot()
        stats = await sa.run_scanner_alert_watch(bot)
        assert stats["posted"] == 0
        assert bot.messages == []
