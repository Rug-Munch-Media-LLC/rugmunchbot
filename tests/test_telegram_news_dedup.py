"""Semantic news dedup — embed lane + cosine collapapse (2026-08-30).

Covers the DeepInfra-first embed lane added to ``channels/telegram_news/_dedup``:

* ``_cosine`` / ``title_overlap`` — pure geometric + Jaccard helpers.
* ``_embed_candidate`` — the DeepInfra → local llama.cpp → vendored fallback
  chain, mocked (no network) so the test asserts the ORDER of fallback and
  that a non-empty vector from any tier wins.
* ``dedupe_articles_semantic`` — the "Trump Digital Gold" paraphrase class
  collapses when the embed lane returns near-identical vectors (the exact
  regression the module was built to catch), while distinct stories are kept.

The tests are offline: every embed tier is monkeypatched.
"""

from __future__ import annotations

import pytest

from rugmunchbot._vendor.news_ingest.models import NewsArticle
from rugmunchbot.channels.telegram_news import _dedup


def _vec(x: float, dim: int = 8) -> list[float]:
    """A deterministic dim-vector whose leading component tracks ``x`` (normalized)."""
    v = [x] + [0.01] * (dim - 1)
    n = sum(a * a for a in v) ** 0.5
    return [a / n for a in v]


def _basis(idx: int, dim: int = 8) -> list[float]:
    """A one-hot-ish basis vector at position ``idx`` (mutually orthogonal)."""
    v = [0.0] * dim
    v[idx % dim] = 1.0
    return v


def test_cosine_pure():
    assert _dedup._cosine([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)
    assert _dedup._cosine([1.0, 0.0, 0.0], [0.0, 1.0, 0.0]) == pytest.approx(0.0)
    assert _dedup._cosine([1.0, 0.0, 0.0], [-1.0, 0.0, 0.0]) == pytest.approx(-1.0)
    assert _dedup._cosine([], [1.0]) == 0.0
    assert _dedup._cosine([1.0], [1.0, 2.0]) == 0.0  # dim mismatch


def test_title_overlap_jaccard():
    assert _dedup.title_overlap("Trump Digital Gold", "Digital Gold Trump") == pytest.approx(1.0)
    assert _dedup.title_overlap("Bitcoin ETF", "Bitcoin ETF") == pytest.approx(1.0)
    assert _dedup.title_overlap("Bitcoin ETF", "Solana memecoin rug") == 0.0


@pytest.mark.asyncio
async def test_embed_candidate_prefers_deepinfra(monkeypatch):
    """DeepInfra returns a vector → chain short-circuits (local + vendored skipped)."""
    called: list[str] = []

    async def _fake_deepinfra(text):
        called.append("deepinfra")
        return _vec(1.0)

    async def _fake_local(text):
        called.append("local")
        return _vec(1.0)

    monkeypatch.setattr(_dedup, "_embed_deepinfra", _fake_deepinfra)
    monkeypatch.setattr(_dedup, "_embed_local", _fake_local)

    vec = await _dedup._embed_candidate("test")
    assert len(vec) == 8
    assert called == ["deepinfra"]


@pytest.mark.asyncio
async def test_embed_candidate_falls_back_to_local(monkeypatch):
    """DeepInfra down → local llama.cpp fallback is tried next."""
    called: list[str] = []

    async def _fake_deepinfra(text):
        called.append("deepinfra")
        return []

    async def _fake_local(text):
        called.append("local")
        return _vec(1.0)

    monkeypatch.setattr(_dedup, "_embed_deepinfra", _fake_deepinfra)
    monkeypatch.setattr(_dedup, "_embed_local", _fake_local)

    vec = await _dedup._embed_candidate("test")
    assert len(vec) == 8
    assert called == ["deepinfra", "local"]


@pytest.mark.asyncio
async def test_dedup_collapses_paraphrase(monkeypatch):
    """Two reworded copies of the same story collapse (the regression case)."""

    # The two articles share a story but differ in URL/title/body — the exact
    # "Trump Digital Gold" shape the semantic layer is meant to catch.
    # Both embed to near-identical vectors so cosine >= threshold.
    async def _fake_candidate(text):
        return _vec(0.9)

    monkeypatch.setattr(_dedup, "_embed_candidate", _fake_candidate)

    a1 = NewsArticle(
        title="Trump's Digital Gold pushes Bitcoin to new high",
        body="The ETF approval drove prices to a record as institutional demand surged.",
        source_channel="beincrypto",
        source_url="https://beincrypto.com/trump-digital-gold",
        category="crypto_news",
    )
    a2 = NewsArticle(
        title="Digital Gold: Trump rally lifts BTC to all-time high",
        body="Spot ETF approval sent the asset to a fresh record as big funds piled in.",
        source_channel="cryptobriefing",
        source_url="https://cryptobriefing.com/digital-gold-rally",
        category="crypto_news",
    )
    kept = await _dedup.dedupe_articles_semantic([a1, a2])
    assert len(kept) == 1


@pytest.mark.asyncio
async def test_dedup_keeps_distinct(monkeypatch):
    """Two genuinely different stories stay separate even with a shared topical term."""

    async def _fake_candidate(text):
        # Distinct stories → orthogonal embeddings (cosine 0 < threshold).
        return _basis(0) if "rug" in text.lower() else _basis(1)

    monkeypatch.setattr(_dedup, "_embed_candidate", _fake_candidate)

    a1 = NewsArticle(
        title="Solana memecoin rug pulls for $2M",
        body="The deployer dumped the LP after the presale.",
        source_channel="rektnews",
        source_url="https://rekt.news/sol-rug",
        category="crypto_news",
    )
    a2 = NewsArticle(
        title="SEC approves spot Bitcoin ETF",
        body="The regulator cleared the first spot ETF listing.",
        source_channel="coindesk",
        source_url="https://coindesk.com/btc-etf",
        category="crypto_news",
    )
    kept = await _dedup.dedupe_articles_semantic([a1, a2])
    assert len(kept) == 2
