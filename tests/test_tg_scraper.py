"""Tests for the all-dialogs TG scraper (channels/tg_scraper).

Covers: dialog listing, message scraping (window filter, sender extraction,
forwarded-from, reply chains, media budget), FloodWait fail-open, dedup,
scam-pattern detection, token-mention extraction, RAG ingestion with stable
doc_ids, and threat persistence. The Telegram client is faked — no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from rugmunchbot.channels.tg_scraper import (
    Detection,
    TgMessage,
    detect_scam_patterns,
    detection_summary,
    extract_mentions,
    list_dialogs,
    message_key,
    run_tg_scraper,
    scrape_all_dialogs,
    scrape_dialog,
)
from rugmunchbot.channels.tg_scraper._ingest import _mark_seen, message_key as _mk
from rugmunchbot.channels.tg_scraper._scrape import _flood_wait_seconds

# ── Fakes ────────────────────────────────────────────────────────────────


@dataclass
class FakeMsg:
    id: int
    message: str = ""
    date: datetime | None = None
    sender_id: int | None = None
    sender: Any = None
    media: Any = None
    fwd_from: Any = None
    reply_to_msg_id: int | None = None
    views: int | None = None
    downloaded: list[str] = field(default_factory=list)

    async def download_media(self, file: str | None = None) -> str:
        self.downloaded.append(str(self.id))
        return f"/tmp/rugmunchbot/fake/{self.id}.jpg"  # noqa: S108 — test fixture path


def _fake_dialog_iter(dialogs: list[Any]) -> Any:
    async def _gen() -> Any:
        for d in dialogs:
            yield d

    return _gen()


class FakeTelethonClient:
    """Duck-typed Telethon client: iter_dialogs + iter_messages (keyed by name).

    Yields SimpleNamespace dialog objects (like Telethon) whose ``name`` keys
    into the message dict.
    """

    def __init__(self, dialogs: dict[str, list[FakeMsg]]) -> None:
        self._dialogs = dialogs
        self.read: list[Any] = []

    def iter_dialogs(self) -> Any:
        return _fake_dialog_iter([_dialog(name, name.lower()) for name in self._dialogs])

    def iter_messages(self, dialog: Any, limit: int = 100) -> Any:
        self.read.append(dialog)

        async def _gen() -> Any:
            for msg in self._dialogs.get(getattr(dialog, "name", None), [])[:limit]:
                yield msg

        return _gen()


def _dialog(name: str, username: str | None = None, is_user: bool = False) -> Any:
    return SimpleNamespace(
        name=name,
        entity=SimpleNamespace(id=abs(hash(name)) % 10**9, username=username),
        is_user=is_user,
        is_group=not is_user,
        is_channel=not is_user,
    )


def _msg(mid: int, text: str = "", hours_ago: float = 1.0, **kw: Any) -> FakeMsg:
    return FakeMsg(
        id=mid,
        message=text,
        date=datetime.now(UTC) - timedelta(hours=hours_ago),
        **kw,
    )


# ── Dialog listing ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_dialogs_skips_private_and_own() -> None:
    client = FakeTelethonClient({})
    dialogs = [
        _dialog("Alpha Calls", "alphacalls"),
        _dialog("Some Guy", is_user=True),
        _dialog("RugMunch Updates", "rugmunch"),
    ]
    client.iter_dialogs = lambda: _fake_dialog_iter(dialogs)
    result = await list_dialogs(client)
    names = [d.name for d in result]
    assert "Alpha Calls" in names
    assert "Some Guy" not in names
    assert all("rugmunch" not in n.lower() for n in names)


@pytest.mark.asyncio
async def test_list_dialogs_fail_open() -> None:
    assert await list_dialogs(None) == []

    class Boom:
        def iter_dialogs(self) -> Any:
            raise RuntimeError("dead")

    assert await list_dialogs(Boom()) == []


# ── Message scraping ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scrape_dialog_window_and_fields() -> None:
    dialog = _dialog("Shill Central", "shillcentral")
    sender = SimpleNamespace(username="@shiller", first_name="Shill", last_name="Bot")
    msgs = [
        _msg(
            1,
            "fresh",
            hours_ago=1,
            sender=sender,
            fwd_from=SimpleNamespace(from_name="orig"),
            reply_to_msg_id=99,
            views=42,
        ),
        _msg(2, "ancient", hours_ago=72),
    ]
    client = FakeTelethonClient({dialog.name: msgs})
    out = await scrape_dialog(client, dialog, lookback_hours=24, per_channel_limit=10)
    assert len(out) == 1
    m = out[0]
    assert m.message_id == 1
    assert m.text == "fresh"
    assert m.sender_handle == "shiller"  # @ stripped
    assert m.sender_name == "Shill Bot"
    assert m.forwarded_from == "orig"
    assert m.reply_to == 99
    assert m.views == 42
    assert m.channel_username == "shillcentral"


@pytest.mark.asyncio
async def test_scrape_dialog_media_budget() -> None:
    dialog = _dialog("Pics", "pics")
    msgs = [_msg(1, "a"), _msg(2, "b"), _msg(3, "c")]
    for m in msgs:

        class Photo:
            """Duck-typed Telethon MessageMediaPhoto (type name contains 'photo')."""

        m.media = Photo()
    client = FakeTelethonClient({dialog.name: msgs})
    out = await scrape_dialog(
        client, dialog, lookback_hours=24, per_channel_limit=10, media_budget=2
    )
    downloaded = [m.message_id for m in out if m.media_path]
    assert len(downloaded) == 2  # budget respected


@pytest.mark.asyncio
async def test_scrape_dialog_flood_wait_short_honored() -> None:
    dialog = _dialog("Flooded", "flooded")

    class _FloodErr(Exception):
        seconds = 2

    class FloodClient2:
        def iter_messages(self, dialog: Any, limit: int = 100) -> Any:
            async def _gen() -> Any:
                raise _FloodErr("flood")
                yield  # pragma: no cover

            return _gen()

    out = await scrape_dialog(FloodClient2(), dialog, lookback_hours=24)
    assert out == []  # fail-open, no raise


@pytest.mark.asyncio
async def test_scrape_dialog_flood_wait_long_skips() -> None:
    dialog = _dialog("Flooded", "flooded")

    class _FloodErr(Exception):
        seconds = 9999

    class FloodClient:
        def iter_messages(self, dialog: Any, limit: int = 100) -> Any:
            async def _gen() -> Any:
                raise _FloodErr("flood")
                yield  # pragma: no cover

            return _gen()

    out = await scrape_dialog(FloodClient(), dialog, lookback_hours=24)
    assert out == []


def test_flood_wait_seconds_extraction() -> None:
    assert _flood_wait_seconds(SimpleNamespace(seconds=30)) == 30.0
    assert _flood_wait_seconds(RuntimeError("x")) is None


@pytest.mark.asyncio
async def test_scrape_all_dialogs_dedup_and_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    # Zero jitter for test speed.
    monkeypatch.setattr("rugmunchbot.channels.tg_scraper._scrape.TG_SCRAPER_JITTER_MAX", 0)
    d1 = _dialog("One", "one")
    d2 = _dialog("Two", "two")
    dup = _msg(7, "dup")
    client = FakeTelethonClient({d1.name: [dup, _msg(1, "a")], d2.name: [dup, _msg(2, "b")]})
    out = await scrape_all_dialogs(client, lookback_hours=24, per_channel_limit=10)
    # Same message_id in different channels is NOT deduped (keyed per channel);
    # the literal same object within one channel is.
    keys = [(m.channel_username, m.message_id) for m in out]
    assert len(keys) == len(set(keys))
    assert ("one", 7) in keys
    assert ("two", 7) in keys


# ── Detection ────────────────────────────────────────────────────────────


def test_extract_mentions() -> None:
    tickers, evm, sol = extract_mentions(
        "Buy $PEPE and $rug now 0x1234567890abcdef1234567890abcdef12345678 "
        "So11111111111111111111111111111111111111112"
    )
    assert "PEPE" in tickers
    assert "RUG" in tickers
    assert evm == ["0x1234567890abcdef1234567890abcdef12345678"]
    assert len(sol) == 1


def test_extract_mentions_empty() -> None:
    assert extract_mentions("") == ([], [], [])


def test_detect_scam_patterns() -> None:
    hits = detect_scam_patterns("Send 1 ETH to get 2 ETH back, guaranteed returns!")
    ids = {h.pattern_id for h in hits}
    assert "doubling_scam" in ids
    assert "guaranteed_returns" in ids
    assert all(h.severity in ("critical", "high", "medium") for h in hits)


def test_detect_scam_patterns_seed_phrase() -> None:
    hits = detect_scam_patterns("Please share your seed phrase to verify your wallet")
    assert any(h.pattern_id == "seed_phrase_harvest" for h in hits)


def test_detect_scam_patterns_clean_text() -> None:
    assert detect_scam_patterns("Bitcoin ETF inflows hit a new record today") == []


@pytest.mark.asyncio
async def test_detect_message_known_scammer(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_probe(handle: str | None) -> bool:
        return handle == "scammer123"

    monkeypatch.setattr("rugmunchbot.channels.tg_scraper._detect.is_known_scammer", fake_probe)
    msg = TgMessage(
        channel="c", channel_username="c", message_id=1, text="hello", sender_handle="scammer123"
    )
    from rugmunchbot.channels.tg_scraper import detect_message

    det = await detect_message(msg)
    assert det.known_scammer
    assert det.is_suspicious


@pytest.mark.asyncio
async def test_detect_message_clean() -> None:
    from rugmunchbot.channels.tg_scraper import detect_message

    msg = TgMessage(
        channel="c", channel_username="c", message_id=2, text="gm", sender_handle="friend"
    )
    det = await detect_message(msg)
    assert not det.is_suspicious


def test_detection_summary() -> None:
    det = Detection(tickers=["PEPE"], hits=detect_scam_patterns("guaranteed returns"))
    summary = detection_summary(det)
    assert "tickers=PEPE" in summary
    assert "guaranteed_returns" in summary


# ── Ingestion ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_seen_dedup(monkeypatch: pytest.MonkeyPatch) -> None:
    from rugmunchbot.channels.tg_scraper import _ingest

    monkeypatch.setattr(_ingest, "_SEEN_CACHE", set())

    class NoRedis:
        pass

    monkeypatch.setattr(
        "rugmunchbot._vendor.redis_client.get_redis_async", lambda: None, raising=False
    )
    assert await _mark_seen("ch:1") is True
    assert await _mark_seen("ch:1") is False  # in-memory dedup


def test_message_key() -> None:
    msg = TgMessage(channel="Title", channel_username="@chan", message_id=5)
    assert _mk(msg) == "chan:5"
    assert message_key(msg) == "chan:5"


@pytest.mark.asyncio
async def test_run_tg_scraper_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full pipeline with fakes: scrape → detect → ingest → persist."""
    from rugmunchbot.channels.tg_scraper import _ingest, _scrape

    dialog = _dialog("Scammy", "scammy")
    scam_msg = _msg(1, "Send 1 ETH get 2 back! $SCAM 0x1234567890abcdef1234567890abcdef12345678")
    news_msg = _msg(2, "Market update: BTC steady")
    client = FakeTelethonClient({dialog.name: [scam_msg, news_msg]})

    async def fake_client() -> Any:
        return client

    monkeypatch.setattr(_scrape, "_build_headless_client", fake_client)
    monkeypatch.setattr("rugmunchbot.channels.tg_scraper._scrape.TG_SCRAPER_JITTER_MAX", 0)

    # RAG: capture docs, accept all.
    ingested_docs: list[Any] = []

    class FakeReport:
        accepted = 1

    class FakeIngestor:
        async def ingest_batch(self, docs: list[Any]) -> Any:
            ingested_docs.extend(docs)
            return FakeReport()

    class FakeDocument:
        def __init__(self, **kw: Any) -> None:
            self.__dict__.update(kw)

    import rugmunchbot._vendor.rag_pkg.ingestion as rag_ing

    monkeypatch.setattr(rag_ing, "BatchIngestor", FakeIngestor, raising=False)
    monkeypatch.setattr(rag_ing, "Document", FakeDocument, raising=False)

    # Redis dedup: in-memory only.
    monkeypatch.setattr(
        "rugmunchbot._vendor.redis_client.get_redis_async", lambda: None, raising=False
    )
    monkeypatch.setattr(_ingest, "_SEEN_CACHE", set())

    # Backend evidence: capture records.
    evidence: list[dict[str, Any]] = []

    class FakeShillApi:
        @staticmethod
        async def add_evidence(record: dict[str, Any]) -> dict[str, Any]:
            evidence.append(record)
            return {"saved": True}

    import rugmunchbot.services as services

    monkeypatch.setattr(services, "shill_api", FakeShillApi, raising=False)

    counts = await run_tg_scraper()
    assert counts["messages"] == 2
    assert counts["suspicious"] == 1
    assert counts["rag"] == 2
    assert counts["threat"] == 1
    # telegram_social doc for the news message + threat doc for the scam.
    collections = {d.collection for d in ingested_docs}
    assert "telegram_social" in collections
    assert "threat_intel_v2" in collections
    doc_ids = {d.doc_id for d in ingested_docs}
    assert "tg:scammy:2" in doc_ids  # stable doc_id
    assert evidence
    assert evidence[0]["source"] == "telegram"
    assert evidence[0]["account"]  # sender attribution present


@pytest.mark.asyncio
async def test_run_tg_scraper_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from rugmunchbot.channels.tg_scraper import _scrape

    async def fake_client() -> Any:
        return None

    monkeypatch.setattr(_scrape, "_build_headless_client", fake_client)
    counts = await run_tg_scraper()
    assert counts == {"messages": 0, "suspicious": 0, "rag": 0, "threat": 0}


@pytest.mark.asyncio
async def test_run_tg_scraper_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second run with the same messages ingests nothing (seen-set dedup)."""
    from rugmunchbot.channels.tg_scraper import _ingest, _scrape

    dialog = _dialog("Ch", "ch")
    client = FakeTelethonClient({dialog.name: [_msg(1, "hello world")]})

    async def fake_client() -> Any:
        return client

    monkeypatch.setattr(_scrape, "_build_headless_client", fake_client)
    monkeypatch.setattr("rugmunchbot.channels.tg_scraper._scrape.TG_SCRAPER_JITTER_MAX", 0)

    class FakeReport:
        accepted = 1

    class FakeIngestor:
        async def ingest_batch(self, docs: list[Any]) -> Any:
            return FakeReport()

    class FakeDocument:
        def __init__(self, **kw: Any) -> None:
            self.__dict__.update(kw)

    import rugmunchbot._vendor.rag_pkg.ingestion as rag_ing

    monkeypatch.setattr(rag_ing, "BatchIngestor", FakeIngestor, raising=False)
    monkeypatch.setattr(rag_ing, "Document", FakeDocument, raising=False)
    monkeypatch.setattr(
        "rugmunchbot._vendor.redis_client.get_redis_async", lambda: None, raising=False
    )
    monkeypatch.setattr(_ingest, "_SEEN_CACHE", set())

    first = await run_tg_scraper()
    assert first["rag"] == 1
    second = await run_tg_scraper()
    assert second["rag"] == 0  # deduped
