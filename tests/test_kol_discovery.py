"""KOL discovery from X shill monitoring — pure detection + persistence.

Covers the 2026-08-31 total-X-KOL-monitoring deliverable:

* ``extract_tweet_tokens`` — cashtag/address extraction, bluechip drop.
* ``detect_new_kols`` — untracked handles pushing risky tokens with
  engagement >= threshold; tracked handles and low engagement excluded.
* ``add_discovered_kol`` / ``load_discovered_kols`` — dedupe by handle,
  evidence merge, atomic write (tmp path).
* ``run_kol_discovery`` — budget-bounded runner with the ApiPass client and
  scanner mocked (no network, no credits burned in tests).
"""

from __future__ import annotations

import rugmunchbot.channels.kol_discovery as kd


def _tweet(
    handle: str, text: str, *, likes: int = 50, retweets: int = 10, tweet_id: str = "1"
) -> dict:
    return {
        "tweet_id": tweet_id,
        "text": text,
        "handle": handle,
        "like_count": likes,
        "retweet_count": retweets,
        "url": f"https://x.com/{handle}/status/{tweet_id}",
        "source": "apipass_x",
    }


# ── extract_tweet_tokens ──────────────────────────────────────────────────


def test_extract_tokens_cashtag_and_address() -> None:
    text = "ape $PEPE now 0xAbCdEf0123456789AbCdEf0123456789AbCdEf01 before $PEPE pumps"
    tokens = kd.extract_tweet_tokens(text)
    assert tokens[0] == "$PEPE"
    assert any(t.startswith("0x") for t in tokens)
    assert tokens.count("$PEPE") == 1  # deduped


def test_extract_tokens_drops_bluechip() -> None:
    assert kd.extract_tweet_tokens("$BTC to the moon $ETH $SOL") == []


# ── detect_new_kols ───────────────────────────────────────────────────────


def test_detect_new_kols_finds_untracked_risky_pusher() -> None:
    tweets = [
        _tweet("newshill", "$RUGG call — 100x incoming", likes=120, retweets=40, tweet_id="111"),
        _tweet("trackedkol", "$RUGG call", likes=500, retweets=100, tweet_id="222"),
    ]
    found = kd.detect_new_kols(tweets, tracked_handles={"trackedkol"}, risky_tokens={"$RUGG"})
    assert len(found) == 1
    rec = found[0]
    assert rec["handle"] == "newshill"
    assert rec["token"] == "$RUGG"
    assert rec["engagement"] == 160
    assert rec["tweet_id"] == "111"


def test_detect_new_kols_engagement_floor() -> None:
    tweets = [_tweet("lowreach", "$RUGG call", likes=3, retweets=1)]
    assert kd.detect_new_kols(tweets, tracked_handles=set(), risky_tokens={"$RUGG"}) == []


def test_detect_new_kols_ignores_unrisky_tokens() -> None:
    tweets = [_tweet("someone", "$SAFE moon", likes=500, retweets=200)]
    assert kd.detect_new_kols(tweets, tracked_handles=set(), risky_tokens={"$RUGG"}) == []


def test_detect_new_kols_address_match_case_insensitive() -> None:
    addr = "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01"
    tweets = [_tweet("shiller", f"entry on {addr}", likes=80, retweets=20, tweet_id="333")]
    found = kd.detect_new_kols(tweets, tracked_handles=set(), risky_tokens={addr.lower()})
    assert len(found) == 1
    assert found[0]["token"].lower() == addr.lower()


def test_detect_new_kols_best_engagement_per_pair() -> None:
    tweets = [
        _tweet("shiller", "$RUGG early", likes=10, retweets=0, tweet_id="1"),
        _tweet("shiller", "$RUGG still early", likes=90, retweets=30, tweet_id="2"),
    ]
    found = kd.detect_new_kols(tweets, tracked_handles=set(), risky_tokens={"$RUGG"})
    assert len(found) == 1
    assert found[0]["engagement"] == 120
    assert found[0]["tweet_id"] == "2"


# ── persistence ───────────────────────────────────────────────────────────


def test_add_and_load_discovered_kol(tmp_path) -> None:
    path = str(tmp_path / "discovered_kols.json")
    assert kd.add_discovered_kol("NewShill", "$RUGG", {"tweet_id": "1", "url": "u"}, path=path)
    # Merge: same handle, new token + evidence appends.
    assert kd.add_discovered_kol("NewShill", "$SCAM", {"tweet_id": "2", "url": "u2"}, path=path)
    records = kd.load_discovered_kols(path)
    assert len(records) == 1
    rec = records[0]
    assert rec["status"] == "discovered"
    assert rec["tokens_shilled"] == ["$RUGG", "$SCAM"]
    assert len(rec["evidence"]) == 2
    # Same tweet_id is not duplicated.
    assert kd.add_discovered_kol("NewShill", "$RUGG", {"tweet_id": "1", "url": "u"}, path=path)
    assert len(kd.load_discovered_kols(path)[0]["evidence"]) == 2


def test_add_discovered_kol_rejects_empty(tmp_path) -> None:
    path = str(tmp_path / "d.json")
    assert kd.add_discovered_kol("", "$RUGG", {}, path=path) is False
    assert kd.add_discovered_kol("handle", "", {}, path=path) is False


# ── runner (mocked I/O) ───────────────────────────────────────────────────


class _FakeApipass:
    """Stand-in for rugmunchbot.tools.apipass_x (no network, no credits)."""

    def __init__(self, *, configured: bool = True, tweets: list[dict] | None = None) -> None:
        self._configured = configured
        self._tweets = tweets or []

    def api_key_configured(self) -> bool:
        return self._configured

    async def search_tweets(self, query: str, limit: int = 10) -> list[dict]:
        return list(self._tweets)


def _install_fake_apipass(monkeypatch, fake: _FakeApipassModule) -> None:
    """Install the fake apipass module for BOTH resolution paths.

    ``from rugmunchbot.tools import apipass_x`` resolves via the package
    attribute once the real submodule has been imported by any earlier test
    (e.g. test_apipass_x) — sys.modules alone is not enough. Patching the
    package attribute keeps the test hermetic (no real X API calls, which
    WOULD fire on a machine with a configured key).
    """
    import sys

    monkeypatch.setitem(sys.modules, "rugmunchbot.tools.apipass_x", fake)
    monkeypatch.setattr("rugmunchbot.tools.apipass_x", fake, raising=False)


async def test_run_kol_discovery_adds_new_kol(tmp_path, monkeypatch) -> None:
    path = str(tmp_path / "discovered_kols.json")
    monkeypatch.setattr(kd, "DISCOVERED_KOLS_PATH", path)

    async def fake_risky(token: str, chain: str) -> bool:
        return True

    monkeypatch.setattr(kd, "_seed_tokens", lambda limit: ["$RUGG"])
    monkeypatch.setattr(kd, "_is_risky_token", fake_risky)
    _install_fake_apipass(
        monkeypatch,
        _FakeApipassModule(
            tweets=[
                _tweet("brandnewkol", "$RUGG 100x call", likes=200, retweets=50, tweet_id="999")
            ]
        ),
    )

    added = await kd.run_kol_discovery(ctx=None)
    assert added == 1
    records = kd.load_discovered_kols(path)
    assert records[0]["handle"] == "brandnewkol"
    assert records[0]["tokens_shilled"] == ["$RUGG"]
    assert records[0]["evidence"][0]["tweet_id"] == "999"


async def test_run_kol_discovery_no_key_noop(monkeypatch) -> None:
    _install_fake_apipass(monkeypatch, _FakeApipassModule(configured=False))
    assert await kd.run_kol_discovery(ctx=None) == 0


async def test_run_kol_discovery_safe_token_not_added(monkeypatch, tmp_path) -> None:
    """A token the scanner does NOT flag risky produces zero discoveries."""
    path = str(tmp_path / "discovered_kols.json")
    monkeypatch.setattr(kd, "DISCOVERED_KOLS_PATH", path)

    async def fake_risky(token: str, chain: str) -> bool:
        return False

    monkeypatch.setattr(kd, "_seed_tokens", lambda limit: ["$SAFE"])
    monkeypatch.setattr(kd, "_is_risky_token", fake_risky)
    _install_fake_apipass(
        monkeypatch,
        _FakeApipassModule(
            tweets=[_tweet("someone", "$SAFE moon", likes=500, retweets=100, tweet_id="7")]
        ),
    )

    assert await kd.run_kol_discovery(ctx=None) == 0
    assert kd.load_discovered_kols(path) == []


class _FakeApipassModule:
    """Module-shaped apipass_x stub (run_kol_discovery imports the module)."""

    def __init__(self, *, configured: bool = True, tweets: list[dict] | None = None) -> None:
        self._inner = _FakeApipass(configured=configured, tweets=tweets)

    def api_key_configured(self) -> bool:
        return self._inner.api_key_configured()

    async def search_tweets(self, query: str, limit: int = 10) -> list[dict]:
        return await self._inner.search_tweets(query, limit=limit)
