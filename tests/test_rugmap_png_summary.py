"""/rugmap — cluster summary caption, PNG metadata strip, handler wiring.

Every backend call is a fake client and every render is either the real
(single) map engine with a synthetic graph or a stub PNG, so the tests cover
the contract without network, Telegram or a live RugMaps index.
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock

import pytest

from rugmunchbot.commands import rugmap as rugmap_mod
from rugmunchbot.commands._rugmap_caption import CAPTION_LIMIT, build_caption
from rugmunchbot.commands._rugmap_summary import build_cluster_summary
from rugmunchbot.services.visuals import render_cluster_map
from rugmunchbot.services.visuals_map_meta import annotate_map_png

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

NODES = [
    {"address": "DevWa11et", "label": "Deployer", "risk_level": "high", "weight": 9, "pct": 4.0},
    {"address": "Bund1erA", "risk_level": "high", "weight": 3, "pct": 2.5},
    {"address": "Bund1erB", "risk_level": "medium", "weight": 2, "pct": 2.0},
    {"address": "Binance", "label": "Binance", "risk_level": "low", "weight": 5},
    {"address": "LoneWallet", "risk_level": "unknown", "weight": 1},
]
LINKS = [
    {"source": "DevWa11et", "target": "Bund1erA"},
    {"source": "Bund1erA", "target": "Bund1erB"},
    {"source": "DevWa11et", "target": "Binance"},
]

ANALYSIS = {
    "verdict": "HIGH_RISK",
    "confidence": 72,
    "summary": "Top holders control 48% of supply; 3 red flags.",
    "source": "rmi_data_engine",
    "bundle_wallets": ["Bund1erA", "Bund1erB"],
}


def _summary(**over: object) -> dict:
    kwargs = {
        "nodes": NODES,
        "links": LINKS,
        "analysis": ANALYSIS,
        "databus": {"fresh_wallet_pct": 61.0},
    }
    kwargs.update(over)
    return build_cluster_summary(**kwargs)


# ── build_cluster_summary ───────────────────────────────────────────────


def test_clusters_group_linked_wallets_largest_first() -> None:
    clusters = _summary()["clusters"]
    assert clusters[0]["wallets"] == 4  # Dev + 2 bundlers + exchange
    assert clusters[0]["risk"] == "high"
    assert clusters[0]["share_pct"] == 8.5
    assert clusters[0]["label"] == "Deployer"


def test_lone_wallet_is_not_a_cluster() -> None:
    addresses = {c["label"] for c in _summary()["clusters"]}
    assert "LoneWallet" not in addresses


def test_no_links_explains_empty_clusters() -> None:
    out = build_cluster_summary(NODES, [], ANALYSIS, {})
    assert out["clusters"] == []
    assert "funding link" in out["cluster_note"]


def test_bundle_confidence_prefers_backend_metric() -> None:
    out = _summary(databus={"bundle_confidence": 81, "fresh_wallet_pct": 10})
    assert out["bundle"] == {"confidence": 81, "note": "backend bundle metric"}


def test_bundle_confidence_derived_from_flagged_wallets() -> None:
    out = _summary(databus={})
    assert out["bundle"]["confidence"] == 75
    assert "2 flagged" in out["bundle"]["note"]


def test_bundle_absent_is_null_with_reason() -> None:
    out = build_cluster_summary(NODES, LINKS, {"verdict": "SAFE"}, {})
    assert out["bundle"]["confidence"] is None
    assert "no bundle metric" in out["bundle"]["note"]


def test_fresh_wallets_absent_is_null_with_reason() -> None:
    out = build_cluster_summary(NODES, LINKS, ANALYSIS, {})
    assert out["fresh"] == {"pct": None, "note": "backend returned no fresh-wallet metric"}


def test_ai_block_carries_verdict_and_source() -> None:
    ai = _summary()["ai"]
    assert ai["verdict"] == "HIGH_RISK"
    assert ai["confidence"] == 72
    assert ai["source"] == "rmi_data_engine"


def test_ai_absent_is_null_with_reason() -> None:
    ai = build_cluster_summary(NODES, LINKS, {}, {})["ai"]
    assert ai["verdict"] is None
    assert "no analysis block" in ai["note"]


# ── build_caption ───────────────────────────────────────────────────────

WEB = "https://rugmunch.io/rugmaps?token=X&chain=solana"


def _caption(summary: dict | None = None, name: str = "Token", symbol: str = "TKN") -> str:
    return build_caption(
        "So11111111111111111111111111111111111111112",
        "solana",
        name,
        symbol,
        250_000.0,
        summary or _summary(),
        WEB,
    )


def test_caption_reports_every_section() -> None:
    text = _caption()
    for needle in ("Top clusters", "Bundle", "Fresh wallets", "AI", "Source"):
        assert needle in text
    assert "rmi_data_engine" in text
    assert "$250,000" in text


def test_caption_prints_null_with_reason() -> None:
    bare = build_cluster_summary([], [], {}, {})
    bare["source"] = "n/a — RugMaps token-graph returned no graph"
    text = build_caption(
        "So11111111111111111111111111111111111111112", "solana", "T", "T", 0.0, bare, WEB
    )
    assert "Top clusters</b> — n/a" in text
    assert "Bundle</b> n/a (backend returned no bundle metric)" in text
    assert "Fresh wallets</b> n/a" in text
    assert "AI</b> n/a (token-graph returned no analysis block)" in text


def test_caption_escapes_token_name() -> None:
    text = _caption(name="<b>pump</b>&", symbol="A<B")
    assert "<b>pump</b>" not in text
    assert "&lt;b&gt;" in text


def test_caption_stays_under_telegram_limit() -> None:
    huge = build_cluster_summary(
        NODES * 40, LINKS * 40, {**ANALYSIS, "summary": "x" * 4000}, {"fresh_wallet_pct": 99.9}
    )
    text = _caption(summary=huge, name="N" * 200, symbol="S" * 100)
    assert len(text) <= CAPTION_LIMIT


# ── annotate_map_png ────────────────────────────────────────────────────


def _map_png() -> bytes:
    png = render_cluster_map(NODES, LINKS, "Token", "TKN", "solana", 1200, 42.0)
    assert png
    assert png.startswith(PNG_MAGIC)
    return png


def test_annotate_appends_metadata_strip() -> None:
    from PIL import Image

    before = Image.open(io.BytesIO(_map_png())).size
    out = annotate_map_png(
        _map_png(),
        {
            "token": "So1111",
            "chain": "solana",
            "timestamp": "2026-10-24 10:00 UTC",
            "risk": "HIGH 74",
            "ai": "HIGH_RISK — top holders control 48%",
        },
    )
    assert out
    assert out.startswith(PNG_MAGIC)
    after = Image.open(io.BytesIO(out)).size
    assert after[0] == before[0]
    assert after[1] > before[1]


def test_annotate_without_meta_is_a_passthrough() -> None:
    png = _map_png()
    assert annotate_map_png(png, {}) == png


@pytest.mark.parametrize("bad", [b"", b"not-a-png"])
def test_annotate_rejects_unusable_input(bad: bytes) -> None:
    assert annotate_map_png(bad, {"token": "x"}) is None


# ── handler wiring (fake client + stub render) ──────────────────────────


class _Resp:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload, self.status_code = payload, status

    def json(self) -> dict:
        return self._payload


class _Client:
    """Fake httpx client: token-graph + databus 200, DexScreener empty."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    async def get(self, url: str, **_: object) -> _Resp:
        self.urls.append(url)
        if "token-graph" in url:
            return _Resp(
                {
                    "nodes": NODES,
                    "links": LINKS,
                    "chain": "solana",
                    "risk_level": "high",
                    "risk_score": 74,
                    "analysis": ANALYSIS,
                }
            )
        if "databus/token" in url:
            return _Resp(
                {
                    "bundle_confidence": 88,
                    "fresh_wallet_pct": 61.0,
                    "top_holders": [{"address": "a", "pct": 12.0}],
                }
            )
        return _Resp(
            {
                "pairs": [
                    {
                        "baseToken": {"symbol": "TKN", "name": "Token"},
                        "liquidity": {"usd": 42000.0},
                        "chainId": "solana",
                    }
                ]
            }
        )


def _update() -> MagicMock:
    up = MagicMock()
    msg = MagicMock()
    msg.chat.send_action = AsyncMock()
    msg.reply_photo = AsyncMock()
    msg.reply_text = AsyncMock()
    up.message = msg
    up.callback_query = None
    return up


def _ctx(args: list[str]) -> MagicMock:
    ctx = MagicMock()
    ctx.args = args
    return ctx


@pytest.mark.asyncio
async def test_handler_posts_annotated_photo_with_summary(monkeypatch) -> None:
    client = _Client()
    monkeypatch.setattr(rugmap_mod, "get_shared_client", lambda _t: client)
    monkeypatch.setattr(rugmap_mod, "_render", AsyncMock(return_value=_map_png()))
    up = _update()

    await rugmap_mod.cmd_rugmap(up, _ctx(["So11111111111111111111111111111111111111112"]))

    up.message.reply_photo.assert_awaited_once()
    kwargs = up.message.reply_photo.await_args.kwargs
    assert kwargs["photo"].read().startswith(PNG_MAGIC)
    assert "Top clusters" in kwargs["caption"]
    assert "88%" in kwargs["caption"]
    buttons = kwargs["reply_markup"].inline_keyboard
    assert any(btn.url and "t.me/share/url" in btn.url for row in buttons for btn in row)
    assert up.message.reply_text.await_count == 0


@pytest.mark.asyncio
async def test_handler_falls_back_to_text_without_graph(monkeypatch) -> None:
    client = _Client()
    monkeypatch.setattr(rugmap_mod, "get_shared_client", lambda _t: client)
    monkeypatch.setattr(rugmap_mod, "_fetch_token_graph", AsyncMock(return_value=None))
    up = _update()

    await rugmap_mod.cmd_rugmap(up, _ctx(["So11111111111111111111111111111111111111112"]))

    up.message.reply_photo.assert_not_awaited()
    text = up.message.reply_text.await_args.args[0]
    assert "Top clusters</b> — n/a" in text
    assert "no graph" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [[], ["not-an-address"]])
async def test_handler_rejects_bad_input_before_any_fetch(monkeypatch, args: list[str]) -> None:
    client = _Client()
    monkeypatch.setattr(rugmap_mod, "get_shared_client", lambda _t: client)
    up = _update()

    await rugmap_mod.cmd_rugmap(up, _ctx(args))

    up.message.reply_photo.assert_not_awaited()
    assert client.urls == []
    assert "Usage" in up.message.reply_text.await_args.args[0]
