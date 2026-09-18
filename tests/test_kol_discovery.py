"""KOL discovery from X shill monitoring — pure detection + persistence.

Covers the 2026-08-31 total-X-KOL-monitoring deliverable:

* ``extract_tweet_tokens`` — cashtag/address extraction, bluechip drop.
* ``detect_new_kols`` — untracked handles pushing risky tokens with
  engagement >= threshold; tracked handles and low engagement excluded.
* ``add_discovered_kol`` / ``load_discovered_kols`` — dedupe by handle,
  evidence merge, atomic write (tmp path).
* ``run_kol_discovery`` — retired no-op (X ingestion owned by rugbrain).
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
        "source": "rugbrain_rag",
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


# ── runner (retired) ──────────────────────────────────────────────────────


async def test_run_kol_discovery_retired_noop() -> None:
    """X ingestion is owned by rugbrain — the runner is a fail-open no-op."""
    assert await kd.run_kol_discovery(ctx=None) == 0
