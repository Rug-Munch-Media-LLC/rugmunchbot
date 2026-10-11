"""RAG-drafts posting contract (2026-08-25 audit regressions).

Covers the P0 fixes from the 2026-08-25 rugmunchbot audit:

* Ordering — a draft whose send fails or whose channel has no chat id must
  NOT be consumed by the dedupe mark (the old code remembered BEFORE
  sending, permanently black-holing those drafts).
* Wiring — ``build_social_spike_post`` must be imported into the scheduler
  registry (the old code called it without importing → NameError every
  social-spike tick).
* Observability — ``channel_rag`` must be registered with ``envelope=True``
  so successful ticks emit a channels:tick line.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from rugmunchbot.channels.scheduler import _core, _posts_rag, _register as reg


@pytest.fixture(autouse=True)
def _fresh_dedupe(monkeypatch):
    """Isolate the global dedupe set per test."""
    monkeypatch.setattr(_core, "_posted_keys", {})
    return


@pytest.fixture(autouse=True)
def _isolate_feed_data(monkeypatch, tmp_path):
    """Redirect the feed mirror to a temp dir for EVERY test.

    The 2026-08-30 feed incident leaked a ``ref-1`` / ``example.com`` fixture
    into the LIVE store (``/var/www/feed.rugmunch.io/data/posts.json``)
    because a mirror test wrote through the production ``FEED_DATA`` default.
    ``FEED_DATA`` is a module global read at call time by
    ``mirror_post_to_feed``; pinning it to a per-test temp dir makes it
    structurally impossible for any ``mirror=True`` draft in this module to
    reach the live mirror.
    """
    monkeypatch.setattr(_posts_rag, "FEED_DATA", str(tmp_path))
    return


_LONG_TEXT_A = (
    "Whale wallet 0xabc moved 12,400 ETH to Binance hot wallets overnight, "
    "the largest single transfer this month. Exchange inflows of this size "
    "have preceded every major distribution window this quarter. "
    "https://example.com/a https://example.com/a2 https://example.com/a3"
)
_LONG_TEXT_B = (
    "The same whale cluster rotated another 8,100 ETH into stablecoins this "
    "morning, extending a three-day distribution streak that desks are now "
    "tracking closely across every major venue. "
    "https://example.com/b https://example.com/b2 https://example.com/b3"
)


def _draft(
    ref: str,
    text: str = (
        "Whale wallet 0xabc moved 12,400 ETH to Binance hot wallets overnight, "
        "the largest single transfer this month. Exchange inflows of this size "
        "have preceded every major distribution window this quarter. "
        "Source: https://example.com/x https://example.com/y https://example.com/z"
    ),
    mirror: bool = False,
) -> dict[str, Any]:
    # ts = now: the 2026-08-26 recency gate rejects undated/stale drafts,
    # so fixtures must present as fresh (that gate has its own tests).
    # Default text carries a source link: the 2026-08-26 no-source gate
    # rejects linkless drafts (that gate has its own tests too).
    return {
        "story_ref": ref,
        "text": text,
        "kind": "story",
        "mirror": mirror,
        "ts": datetime.now(UTC).isoformat(),
    }


class _Recorder:
    def __init__(self, succeed: bool = True):
        self.calls: list[tuple[str, str]] = []
        self.succeed = succeed

    async def __call__(self, bot: Any, chat_id: str, text: str, **kw: Any) -> bool:
        self.calls.append((chat_id, text))
        return self.succeed


def _patch_env(
    monkeypatch,
    *,
    chat_id: str | None,
    drafts: list[dict[str, Any]] | dict[str, list[dict[str, Any]]],
    sender: _Recorder,
):
    """Patch the posting env. ``drafts`` may be a flat list (served to every
    channel) or a per-channel mapping like production curation."""
    if isinstance(drafts, list):
        table = dict.fromkeys(("main", "news", "alerts", "scans"), drafts)
    else:
        table = {ch: drafts.get(ch, []) for ch in ("main", "news", "alerts", "scans")}
    async def _no_rag_news(limit=5):
        return []

    monkeypatch.setattr(_posts_rag, "fetch_rag_drafts", _fake_fetch(table))
    monkeypatch.setattr(_posts_rag, "_chat_id", lambda ch: chat_id)
    monkeypatch.setattr(_posts_rag, "_safe_send", sender)
    # RugBrain news_v2 rail is the first fallback tier (fail-open → []).
    # Stub it so tests never hit the network when the RAG lane is empty.
    monkeypatch.setattr(
        "rugmunchbot.channels.content.fetch_rag_news_items", _no_rag_news
    )


def _fake_fetch(table: dict[str, list[dict[str, Any]]]):
    async def _fetch(channel: str, limit: int = 3) -> list[dict[str, Any]]:
        return [dict(d) for d in table.get(channel, [])][:limit]

    return _fetch


class _Bot:
    """Stub bot that records sends — the quality gate may attach a
    placeholder image, routing delivery through the photo-aware path
    (bot.send_photo) instead of the injected _safe_send recorder."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self._fail = fail

    async def send_photo(self, chat_id, photo=None, caption=None, **k):
        if self._fail:
            raise RuntimeError("bot send_photo disabled")
        self.calls.append((chat_id, str(caption or "")))
        return True

    async def send_message(self, chat_id, text=None, **k):
        if self._fail:
            raise RuntimeError("bot send_message disabled")
        self.calls.append((chat_id, str(text or "")))
        return True


class _Ctx:
    def __init__(self, *, bot: _Bot | None = None) -> None:
        self.bot = bot or _Bot()


def _total_sends(sender: _Recorder, bot: _Bot) -> int:
    return len(sender.calls) + len(bot.calls)


@pytest.mark.asyncio
async def test_successful_send_marks_and_posts(monkeypatch):
    sender = _Recorder(succeed=True)
    ctx = _Ctx()
    _patch_env(monkeypatch, chat_id="-100123", drafts=[_draft("ref-1")], sender=sender)

    posted = await _posts_rag.post_rag_drafts(ctx)

    assert posted == 1
    assert _total_sends(sender, ctx.bot) == 1
    # Marked seen: a second identical tick sends nothing more.
    await _posts_rag.post_rag_drafts(ctx)
    assert _total_sends(sender, ctx.bot) == 1


@pytest.mark.asyncio
async def test_failed_send_is_not_burned_next_tick_retries(monkeypatch):
    """THE blackhole regression: failed send must release the dedupe mark."""
    failing = _Recorder(succeed=False)
    ctx = _Ctx(bot=_Bot(fail=True))  # photo path fails too → delivery fails
    _patch_env(
        monkeypatch,
        chat_id="-100123",
        drafts={"main": [_draft("ref-1")], "news": [], "alerts": []},
        sender=failing,
    )

    posted = await _posts_rag.post_rag_drafts(ctx)
    assert posted == 0

    # Next tick: same draft must be retried, not silently skipped.
    recovering = _Recorder(succeed=True)
    ctx2 = _Ctx()
    _patch_env(
        monkeypatch,
        chat_id="-100123",
        drafts={"main": [_draft("ref-1")], "news": [], "alerts": []},
        sender=recovering,
    )
    posted = await _posts_rag.post_rag_drafts(ctx2)
    assert posted == 1
    assert _total_sends(recovering, ctx2.bot) == 1


@pytest.mark.asyncio
async def test_missing_chat_id_does_not_consume_drafts(monkeypatch):
    """No chat id → nothing sent AND nothing marked (old code burned them)."""
    sender = _Recorder(succeed=True)
    _patch_env(monkeypatch, chat_id=None, drafts=[_draft("ref-1")], sender=sender)

    posted = await _posts_rag.post_rag_drafts(_Ctx())

    assert posted == 0
    assert sender.calls == []
    assert _core._posted_keys.get("__global__", {}) == {}


@pytest.mark.asyncio
async def test_per_tick_cap_and_mirror_only_on_main(monkeypatch, tmp_path):
    monkeypatch.setattr(_posts_rag, "MAX_PER_CHANNEL_PER_TICK", 1)
    monkeypatch.setattr(
        _posts_rag,
        "FEED_DATA",
        str(tmp_path),
    )
    # Production curation: only the main lane has drafts this tick.
    sender = _Recorder(succeed=True)
    ctx = _Ctx()
    _patch_env(
        monkeypatch,
        chat_id="-100main",
        drafts={"main": [_draft(f"ref-{i}") for i in range(3)], "news": [], "alerts": []},
        sender=sender,
    )

    posted = await _posts_rag.post_rag_drafts(ctx)

    assert posted == 1  # capped at 1/tick/channel
    assert _total_sends(sender, ctx.bot) == 1


@pytest.mark.asyncio
async def test_cross_channel_duplicate_posts_once_main_wins(monkeypatch):
    """Same story queued for main AND news → exactly one post, and it is the
    flagship (main) that claims it (loop order + global dedupe)."""
    seen_channels: list[str] = []

    async def _send(bot: Any, chat_id: str, text: str, **kw: Any) -> bool:
        seen_channels.append(chat_id)
        return True

    ctx = _Ctx()
    _patch_env(
        monkeypatch,
        chat_id="-100x",
        drafts={
            "main": [_draft("dup-1", text=_LONG_TEXT_A)],
            "news": [_draft("dup-1", text=_LONG_TEXT_B)],
            "alerts": [],
        },
        sender=_send,
    )

    posted = await _posts_rag.post_rag_drafts(ctx)

    assert posted == 1
    delivered = seen_channels + [c for c, _t in ctx.bot.calls]
    assert delivered == ["-100x"]  # main's chat id — the news copy skipped


@pytest.mark.asyncio
async def test_main_mirror_writes_feed_json(monkeypatch, tmp_path):
    monkeypatch.setattr(_posts_rag, "FEED_DATA", str(tmp_path))
    sender = _Recorder(succeed=True)
    _patch_env(
        monkeypatch,
        chat_id="-100main",
        drafts=[_draft("ref-m", text=_LONG_TEXT_A, mirror=True)],
        sender=sender,
    )

    posted = await _posts_rag.post_rag_drafts(_Ctx())

    assert posted == 1
    feed_file = tmp_path / "posts.json"
    assert feed_file.exists()
    data = json.loads(feed_file.read_text())
    assert data[0]["text"] == _LONG_TEXT_A


def test_social_spike_post_is_imported_into_registry():
    """Regression: _social_tick called build_social_spike_post without an
    import → NameError on every tick (live in prod 2026-08-24/25)."""
    assert hasattr(reg, "build_social_spike_post")
    assert callable(reg.build_social_spike_post)


def test_channel_rag_job_registered():
    """channel_rag must appear on the JobQueue when the feature flag is on."""

    class _JQ:
        def __init__(self):
            self.repeating: list[dict[str, Any]] = []
            self.once: list[dict[str, Any]] = []

        def run_repeating(self, callback, interval, first=None, name=None, **kw):
            self.repeating.append({"callback": callback, "name": name, "interval": interval})

        def run_once(self, callback, when, name=None, **kw):
            self.once.append({"name": name})

        def run_daily(self, callback, time, name=None, **kw):  # pragma: no cover
            pass

    class _App:
        def __init__(self):
            self.job_queue = _JQ()

    app = _App()
    monkey_flags = {
        "CHANNEL_JOBS_ENABLED": True,
        "ALERTS_CHANNEL_ENABLED": False,
        "ALPHA_CHANNEL_ENABLED": False,
        "SCAN_CHANNEL_ENABLED": False,
        "TG_NEWS_ENABLED": False,
        "SCAM_WATCH_ENABLED": False,
        "PERSONAL_CHANNEL_ENABLED": False,
        "DAILY_MARKET_SUMMARY_ENABLED": False,
    }
    originals = {k: getattr(reg, k) for k in monkey_flags}
    try:
        for k, v in monkey_flags.items():
            setattr(reg, k, v)
            reg.register_channel_jobs(app)
    finally:
        for k, v in originals.items():
            setattr(reg, k, v)
    names = [j["name"] for j in app.job_queue.repeating]
    assert "channel_rag" in names
    # No no-op content_rotation jobs may come back.
    assert not any(n.startswith("content_") for n in names)


# ── 2026-08-26 no-source incident + scans lane wiring ─────────────────────


def test_source_gate_rejects_linkless_draft():
    """THE no-source regression: 'Source: (none provided)' reached @rmialerts
    because nothing rejected a draft without any link."""
    linkless = _draft("ref-ns", text="A contract bug allowed a $190M drain.")
    assert _posts_rag._source_gate(linkless) == "no_source_link"
    sourced = _draft("ref-ok", text="Drain happened. Source: https://rekt.news/x")
    assert _posts_rag._source_gate(sourced) == ""


@pytest.mark.asyncio
async def test_linkless_draft_is_gated_not_posted(monkeypatch):
    """End-to-end: a draft with no link must never reach the sender, and must
    stay retryable (not burned by the dedupe mark)."""
    sender = _Recorder(succeed=True)
    _patch_env(
        monkeypatch,
        chat_id="-100123",
        drafts={
            "main": [_draft("ref-ns", text="no link here")],
            "news": [],
            "alerts": [],
            "scans": [],
        },
        sender=sender,
    )

    posted = await _posts_rag.post_rag_drafts(_Ctx())

    assert posted == 0
    assert sender.calls == []
    assert _core._posted_keys.get("__global__", {}) == {}


@pytest.mark.asyncio
async def test_scans_lane_posts_scanner_drafts(monkeypatch):
    """Wiring regression: rugbrain generates ``scans`` drafts but the bot's
    loop never consumed the lane — scanner reports were dead-queued."""
    scan = {
        "story_ref": "scanner_feedback:0xabc123",
        "text": "[SCAN] SOL · 0xabc…def456\nRisk score: 87/100 (high)\nFull scan output: https://rugmunch.io/scan?token=0xabc",
        "kind": "scan",
        "mirror": False,
        "ts": datetime.now(UTC).isoformat(),
    }
    seen: list[tuple[str, str]] = []

    async def _send(bot: Any, chat_id: str, text: str, **kw: Any) -> bool:
        seen.append((chat_id, text))
        return True

    # The main channel is empty in this fixture, so the unified main gather
    # would otherwise hit the LIVE trending-news backend + reranker. Stub it
    # to [] to keep the test isolated on the scans lane (the fallback path has
    # its own dedicated tests).
    async def _no_news(limit=5):
        return []

    monkeypatch.setattr("rugmunchbot.channels.content.fetch_trending_news", _no_news)
    _patch_env(
        monkeypatch,
        chat_id="-100scans",
        drafts={"main": [], "news": [], "alerts": [], "scans": [scan]},
        sender=_send,
    )

    ctx = _Ctx()
    posted = await _posts_rag.post_rag_drafts(ctx)

    assert posted == 1
    # Image doctrine (2026-08-31): scans-lane drafts ship photo+caption via
    # the photo-aware sender (the gate resolves a placeholder image), so the
    # delivery lands on bot.send_photo, not the text-only _safe_send stub.
    sends = seen + ctx.bot.calls
    assert len(sends) == 1
    assert sends[0][1].startswith("[SCAN]")


# ── 2026-09-03 news-channel content-type doctrine ───────────────────────────


def test_news_content_type_gate_blocks_non_news_kinds():
    """@rmicryptonews is breaking-crypto-news ONLY. Whale alerts, scan
    results, system notifications, product updates, shill/alpha intel and
    roundup/digest summaries must never land on the news channel — the
    content-type gate blocks them regardless of quality/freshness."""
    # News kinds pass.
    assert _posts_rag._news_content_type_gate({"kind": "story"}) == ""
    assert _posts_rag._news_content_type_gate({"kind": "crypto_news"}) == ""
    assert _posts_rag._news_content_type_gate({"kind": "news"}) == ""
    # Unknown / missing kinds pass (the other gates still apply).
    assert _posts_rag._news_content_type_gate({}) == ""
    assert _posts_rag._news_content_type_gate({"kind": "some_future_kind"}) == ""
    # Non-news kinds are blocked.
    for blocked in (
        "whale_move",
        "whale_alert",
        "scan",
        "scanner_alert",
        "alert",
        "exploit",
        "rug-update",
        "scammer_warning",
        "shill_evidence",
        "kol_trade",
        "early_entry",
        "alpha",
        "quality",
        "roundup",
        "digest",
        "market-summary",
        "product_update",
        "changelog",
    ):
        assert _posts_rag._news_content_type_gate({"kind": blocked}) == f"blocked_kind({blocked})"


@pytest.mark.asyncio
async def test_news_lane_blocks_non_news_kinds(monkeypatch):
    """End-to-end: a whale/scan/alert/product-update draft queued for the
    news channel is dropped; a story-kind draft still posts."""
    whale = {
        "story_ref": "whale:0xabc",
        "text": "Whale moved 12,400 ETH to Binance. https://example.com/whale",
        "kind": "whale_move",
        "mirror": False,
        "ts": datetime.now(UTC).isoformat(),
    }
    story = {
        "story_ref": "news:story-1",
        "text": (
            "Ethereum spot ETF inflows hit a record this week as institutional "
            "demand accelerates, with desks now tracking the largest single-day "
            "accumulation since launch. https://example.com/story"
        ),
        "kind": "story",
        "mirror": False,
        "ts": datetime.now(UTC).isoformat(),
    }
    seen: list[tuple[str, str]] = []

    async def _send(bot: Any, chat_id: str, text: str, **kw: Any) -> bool:
        seen.append((chat_id, text))
        return True

    async def _no_news(limit=5):
        return []

    async def _no_main_drafts(limit=8):
        return []

    monkeypatch.setattr("rugmunchbot.channels.content.fetch_trending_news", _no_news)
    monkeypatch.setattr(_posts_rag, "gather_main_drafts", _no_main_drafts)
    _patch_env(
        monkeypatch,
        chat_id="-100news",
        drafts={"main": [], "news": [whale, story], "alerts": [], "scans": []},
        sender=_send,
    )

    ctx = _Ctx()
    posted = await _posts_rag.post_rag_drafts(ctx)
    print("DEBUG posted:", posted, "seen:", seen, "bot.calls:", ctx.bot.calls)

    # Only the story-kind draft posts; the whale_move draft is filtered out.
    assert posted == 1
    sends = seen + ctx.bot.calls
    assert len(sends) == 1
    assert "Ethereum spot ETF" in sends[0][1]


# ── 2026-08-30 unified main content path (task 1/2/3) ──────────────────────


def test_quality_gate_floors_reranked_importance():
    """Task 2: the 0.30 importance floor must read ``importance`` too.

    The fallback drafts used to carry NO score, so ``_quality_gate`` waved
     them through even though the live reranker scored them 0.01-0.12. Routing
     them through ``rerank_quality`` writes ``importance``; this proves the
    gate now floors it (no unscored bypass)."""
    below = _draft("ref-low", text="Altcoin tape. Source: https://example.com/low")
    below["importance"] = 0.12
    assert _posts_rag._quality_gate(below) == "below_threshold(0.12)"

    # A score under the floor is rejected regardless of which key it lives in.
    low_score = _draft("ref-score", text="Headline. Source: https://x.site/a")
    low_score["score"] = 0.05
    assert _posts_rag._quality_gate(low_score) == "below_threshold(0.05)"

    # Above the floor or unscored → allowed (missing score = no signal, pass).
    ok = _draft("ref-ok", text="Headline. Source: https://x.site/b")
    ok["importance"] = 0.62
    assert _posts_rag._quality_gate(ok) == ""
    assert _posts_rag._quality_gate(_draft("ref-none")) == ""


@pytest.mark.asyncio
async def test_main_empty_falls_back_scored_and_warns(monkeypatch, caplog):
    """Task 3 + task 1: when rugbrain returns 0 main drafts, the unified
    pipeline WARNs and folds in the scored bot-sourced fallback (not a silent
    no-op), and the fallback draft posts carrying a real ``importance``."""
    import logging

    async def _news(limit=5):
        return [
            {
                "title": "Bridge exploit drains $190M",
                "summary": "Cross-chain bridge compromised; ~$190M drained.",
                "url": "https://rekt.news/bridge-190m",
                "source": "rekt",
                "sentiment_label": "",
                "factual_rating": "",
            }
        ]

    async def _rerank(items):
        for it in items:
            it["importance"] = 0.66
        return sorted(items, key=lambda i: i.get("importance", 0.0), reverse=True)

    monkeypatch.setattr("rugmunchbot.channels.content.fetch_trending_news", _news)
    monkeypatch.setattr("rugmunchbot.channels.scheduler._posts_rag.rerank_quality", _rerank)

    async def _long_llm(messages, tier):
        # 2026-09-04 main-channel doctrine: >200 chars AND >=3 source links.
        return (
            "A quiet tape hides real rotation: stablecoin inflows hit a "
            "monthly high while perp funding stayed flat, the classic "
            "pre-move accumulation signature desks watch for. "
            "https://rekt.news/bridge-190m "
            "https://rekt.news/bridge-190m-analysis "
            "https://rekt.news/bridge-190m-postmortem"
        )

    monkeypatch.setattr("rugmunchbot.llm_config.chat_with_fallback", _long_llm, raising=False)

    sender = _Recorder(succeed=True)
    _patch_env(
        monkeypatch,
        chat_id="-100main",
        drafts={"main": [], "news": [], "alerts": [], "scans": []},
        sender=sender,
    )

    ctx = _Ctx()
    with caplog.at_level(logging.WARNING, logger="rmi_bot.channels"):
        posted = await _posts_rag.post_rag_drafts(ctx)

    assert posted == 1
    assert _total_sends(sender, ctx.bot) == 1
    delivered = [c for c, _t in sender.calls] + [c for c, _t in ctx.bot.calls]
    assert delivered == ["-100main"]
    # The fallback draft now CARRIES a score (no unscored bypass).
    assert any("rag_lane_empty" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_main_empty_and_fallback_all_below_floor_posts_nothing(monkeypatch):
    """Task 2: if every fallback headline reranks below 0.30, the floor
    discards ALL of them — low-signal fresh headlines never reach main."""

    async def _news(limit=5):
        return [
            {
                "title": "Altcoin tape chatter",
                "summary": "A quiet day in a thinly-traded microcap.",
                "url": "https://example.com/low",
                "source": "example",
                "sentiment_label": "",
                "factual_rating": "",
            }
        ]

    async def _rerank(items):
        for it in items:
            it["importance"] = 0.04  # below every floor
        return sorted(items, key=lambda i: i.get("importance", 0.0), reverse=True)

    monkeypatch.setattr("rugmunchbot.channels.content.fetch_trending_news", _news)
    monkeypatch.setattr("rugmunchbot.channels.scheduler._posts_rag.rerank_quality", _rerank)
    sender = _Recorder(succeed=True)
    _patch_env(
        monkeypatch,
        chat_id="-100main",
        drafts={"main": [], "news": [], "alerts": [], "scans": []},
        sender=sender,
    )

    posted = await _posts_rag.post_rag_drafts(_Ctx())

    assert posted == 0
    assert sender.calls == []
