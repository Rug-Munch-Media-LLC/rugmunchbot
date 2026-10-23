"""Live-source citations, no-source refusal, and long-history compression.

Covers the three guarantees the chat pipeline must keep:
  1. every live number is attributed to the provider that fetched it,
  2. a live-data question with no live source is REFUSED deterministically,
  3. a long conversation is compressed (not blindly truncated) with its
     addresses/tickers preserved.
"""

from __future__ import annotations

from rugmunchbot.handlers.chat_ai._llm import _history
from rugmunchbot.handlers.chat_ai._llm._citations import tool_citations
from rugmunchbot.handlers.chat_ai._llm._history import compress_history
from rugmunchbot.handlers.chat_ai._llm._refusal import no_source_refusal
from rugmunchbot.tools.guardrails import NO_LIVE_DATA_MESSAGE
from rugmunchbot.tools.registry import ToolResult

ADDR = "0x" + "a1" * 20


def _res(tool: str, ok: bool, source: str, data: object = None) -> ToolResult:
    return ToolResult(tool=tool, ok=ok, source=source, data=data)


# ── 1. Citations ─────────────────────────────────────────────────────────


def test_citation_names_the_provider_that_answered():
    results = {"price_lookup": _res("price_lookup", True, "dexscreener:/latest/dex/tokens")}
    assert tool_citations(results) == [
        {"title": "DexScreener", "url": "https://dexscreener.com", "type": "live_tool"}
    ]


def test_citation_skips_failed_internal_and_synthesised_sources():
    results = {
        "price_lookup": _res("price_lookup", False, "dexscreener:/defi/price"),
        "security_scan": _res("security_scan", True, "backend:/api/v1/bot/scan"),
        "web_search": _res("web_search", True, "knowledge-fallback"),
        "smart_money": _res("smart_money", True, "cache:scanresult"),
    }
    assert tool_citations(results) == []


def test_citation_dedupes_one_provider_across_tools():
    results = {
        "price_lookup": _res("price_lookup", True, "dexscreener:/defi/price"),
        "trending": _res("trending", True, "dexscreener:token-boosts"),
        "smart_money": _res("smart_money", True, "birdeye:/smart-money/v1/token/list"),
    }
    titles = [c["title"] for c in tool_citations(results)]
    assert titles == ["DexScreener", "Birdeye"]


# ── 2. No-source refusal ─────────────────────────────────────────────────


def test_refuses_price_question_when_source_failed():
    results = {"price_lookup": _res("price_lookup", False, "dexscreener:/defi/price")}
    assert no_source_refusal("whats the price of $BONK", results) == NO_LIVE_DATA_MESSAGE


def test_refuses_when_no_tool_ran_for_a_routed_data_question():
    assert no_source_refusal("trending tokens on solana", {}) == NO_LIVE_DATA_MESSAGE


def test_allows_answer_when_the_routed_tool_returned_live_data():
    results = {"price_lookup": _res("price_lookup", True, "dexscreener:/defi/price", {"price": 1})}
    assert no_source_refusal("whats the price of $BONK", results) is None


def test_knowledge_question_is_never_refused():
    assert no_source_refusal("what is a rug pull", {}) is None


def test_synthesised_web_answer_cannot_vouch_for_a_price():
    results = {
        "price_lookup": _res("price_lookup", False, "dexscreener:/defi/price"),
        "web_search": _res("web_search", True, "knowledge-fallback", {"context": "..."}),
    }
    assert no_source_refusal("whats the price of $BONK", results) == NO_LIVE_DATA_MESSAGE


# ── 3. Long-history compression ──────────────────────────────────────────


class _StubResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _StubClient:
    """Minimal httpx.AsyncClient stand-in (records the call, never dials)."""

    def __init__(self, payload: dict | None = None, exc: Exception | None = None) -> None:
        self.payload = payload or {}
        self.exc = exc
        self.calls: list[dict] = []

    async def __aenter__(self) -> _StubClient:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def post(self, url: str, json: dict | None = None) -> _StubResponse:
        self.calls.append({"url": url, "json": json or {}})
        if self.exc is not None:
            raise self.exc
        return _StubResponse(self.payload)


def _long_history() -> list[dict[str, str]]:
    tail = "the conversation drifted into methodology, thresholds and weighting " * 4
    return [
        {"role": "user", "content": f"I hold {ADDR} and also $WIF — keep that in mind. {tail}"},
        {"role": "assistant", "content": f"Noted: {ADDR} and $WIF. {tail}"},
        {"role": "user", "content": f"compare it with $BONK {tail}"},
        {"role": "assistant", "content": tail},
    ]


async def test_history_compression_uses_lane_and_reattaches_facts(monkeypatch):
    monkeypatch.setenv("CC_COMPRESS_CHAT_HISTORY", "1")
    monkeypatch.delenv("CC_COMPRESS_URL", raising=False)
    monkeypatch.delenv("CC_COMPRESS_PACKS_URL", raising=False)
    # Lane output that lost the address on purpose — the local check must win.
    stub = _StubClient({"compressed_prompt": "user asked about $WIF", "identifier_guard": "passed"})
    monkeypatch.setattr(_history.httpx, "AsyncClient", lambda **_: stub)

    block = await compress_history(_long_history())

    assert stub.calls
    assert stub.calls[0]["json"]["rate"] == 0.5
    assert block.startswith("user asked about $WIF")
    assert ADDR in block, "an identifier dropped by the extractive pass must be re-attached"


async def test_history_compression_fails_open(monkeypatch):
    monkeypatch.setenv("CC_COMPRESS_CHAT_HISTORY", "1")
    stub = _StubClient(exc=RuntimeError("8077 down"))
    monkeypatch.setattr(_history.httpx, "AsyncClient", lambda **_: stub)

    block = await compress_history(_long_history())

    # Deterministic block survived, both roles rendered, no raise.
    assert "[user]" in block
    assert "[assistant]" in block
    assert block != ""


async def test_history_compression_is_disabled_by_env(monkeypatch):
    monkeypatch.setenv("CC_COMPRESS_CHAT_HISTORY", "0")
    stub = _StubClient(exc=AssertionError("lane must not be called"))
    monkeypatch.setattr(_history.httpx, "AsyncClient", lambda **_: stub)

    block = await compress_history(_long_history())

    assert stub.calls == []
    assert ADDR in block


async def test_empty_history_yields_no_block():
    assert await compress_history([]) == ""
    assert await compress_history(None) == ""


# ── 4. Wire-level: the fallback path cites the tool and can refuse ───────


def _task(value: object):
    """A finished asyncio.Task carrying ``value`` (the pipeline awaits tasks)."""
    import asyncio

    async def _run() -> object:
        return value

    return asyncio.create_task(_run())


async def test_fallback_refuses_and_skips_llm_when_no_live_source(monkeypatch):
    from rugmunchbot.handlers.chat_ai._llm import _fallback

    called: list[str] = []

    async def _no_llm(*args, **kwargs):
        called.append("llm")
        return {"message": "invented", "provider": "x"}

    monkeypatch.setattr(_fallback.chat_ai, "_call_remote_llm", _no_llm)
    live = {"price_lookup": _res("price_lookup", False, "dexscreener:/defi/price")}

    result, sources, _actions, _rag, _market = await _fallback._build_fallback_answer(
        "whats the price of $BONK",
        type("U", (), {"id": 7})(),
        [],
        daily_ctx_task=_task(None),
        scan_ctx_task=None,
        bridge_ctx_task=_task(None),
        live_tools_task=_task(live),
        rag_task=None,
        router_ctx_task=_task(("", [])),
    )

    assert called == [], "no LLM call may happen when the live source is missing"
    assert result["message"] == NO_LIVE_DATA_MESSAGE
    assert sources == []


async def test_fallback_merges_tool_citation_into_sources(monkeypatch):
    from rugmunchbot.handlers.chat_ai._llm import _fallback

    captured: dict = {}

    async def _llm(_text, _uid, _history, sources=None, **_kw):
        captured["sources"] = sources or []
        return {"message": "ok", "provider": "snappy", "rag_count": len(sources or [])}

    monkeypatch.setattr(_fallback.chat_ai, "_call_remote_llm", _llm)
    live = {
        "price_lookup": _res(
            "price_lookup", True, "dexscreener:/defi/price", {"pairs": [{"symbol": "BONK"}]}
        )
    }

    result, sources, *_ = await _fallback._build_fallback_answer(
        "whats the price of $BONK",
        type("U", (), {"id": 7})(),
        [],
        daily_ctx_task=_task(None),
        scan_ctx_task=None,
        bridge_ctx_task=_task(None),
        live_tools_task=_task(live),
        rag_task=None,
        router_ctx_task=_task(("", [])),
    )

    assert result["message"] == "ok"
    assert any(s.get("url") == "https://dexscreener.com" for s in sources)
    assert any(s.get("url") == "https://dexscreener.com" for s in captured["sources"])
