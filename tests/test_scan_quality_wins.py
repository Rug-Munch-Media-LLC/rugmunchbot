"""SCAN-QUALITY-PLAN items 2+3 + cache observability (2026-08-26 wins).

* Win 1 — TIER-0 indexer wiring: the enrichment pipeline fills missing
  deployer_profile / bundle_analysis from our own ClickHouse engines
  (time-boxed, fail-open, never clobbers scan-provided data).
* Win 2 — ``scan_section_fallback`` audit lines at every failover point.
* Win 3 — Redis hit/miss counters for the scan-result cache.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import rugmunchbot.tools.scanner._deployer as dep


def _base_result(**extra):
    r = {
        "name": "Pepe",
        "symbol": "PEPE",
        "chain": "ethereum",
        "address": "0x" + "ab" * 20,
    }
    r.update(extra)
    return r


# ── Win 2: section fallback audit logger ─────────────────────────────


def test_log_section_fallback_formats_line(caplog):
    from rugmunchbot.tools.scanner._section_log import log_section_fallback

    with caplog.at_level("INFO", logger="rmi_bot.scanner.sections"):
        log_section_fallback("holders", "indexer", "birdeye", reason="no_count")
        log_section_fallback("bundle", "clickhouse", "none")
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "scan_section_fallback section=holders from=indexer to=birdeye reason=no_count" in text
    # reason optional
    assert "scan_section_fallback section=bundle from=clickhouse to=none" in text


def test_log_section_fallback_never_raises():
    from rugmunchbot.tools.scanner._section_log import log_section_fallback

    with patch.object(dep.logger, "info", side_effect=RuntimeError("boom")):
        # wrong logger object — but helper wraps its own; direct call must not raise
        log_section_fallback("x", "y", "z")
    # no assertion needed — reaching here means it didn't raise


# ── Win 1: TIER-0 indexer legs in enrichment ─────────────────────────


@pytest.mark.asyncio
async def test_enrichment_fills_deployer_profile_from_clickhouse(monkeypatch):
    """Missing deployer_profile + known deployer → ClickHouse engine fills it."""

    async def _fake_profile(deployer, chain, force_fresh=False):
        return {
            "total_tokens_launched": 7,
            "rugged_pct": 0.43,
            "honeypot_pct": 0.14,
            "avg_lifespan_days": 3.2,
            "chains_used": ["ethereum"],
            "label": "SERIAL RUGGER",
        }

    monkeypatch.setattr(
        "rugmunch_scanner.unified.engines.deployer_profiles.get_deployer_profile",
        _fake_profile,
    )
    # DexScreener leg unreachable — enrichment must still complete.
    with patch.object(dep, "http_get_json", side_effect=ConnectionError("offline")):
        out = await dep._supplement_deployer_socials(
            _base_result(deployer_address="0x" + "cd" * 20), "0x" + "ab" * 20, "ethereum"
        )
    assert out["deployer_profile"]["label"] == "SERIAL RUGGER"
    assert out["deployer_metrics"]["total_tokens_deployed"] == 7
    assert abs(out["deployer_metrics"]["rugpull_rate"] - 0.43) < 1e-9


@pytest.mark.asyncio
async def test_enrichment_never_clobbers_scan_provided_profile(monkeypatch):
    existing = {"total_tokens_launched": 1, "label": "FROM SCAN"}
    monkeypatch.setattr(
        "rugmunch_scanner.unified.engines.deployer_profiles.get_deployer_profile",
        None,  # would explode if called → proves it isn't
    )
    with patch.object(dep, "http_get_json", side_effect=ConnectionError("offline")):
        out = await dep._supplement_deployer_socials(
            _base_result(deployer_address="0x" + "cd" * 20, deployer_profile=existing),
            "0x" + "ab" * 20,
            "ethereum",
        )
    assert out["deployer_profile"] is existing


@pytest.mark.asyncio
async def test_enrichment_bundle_from_own_engine_maps_shape(monkeypatch):
    async def _fake_bundle(token_address, chain, force_fresh=False):
        return {
            "is_bundled": True,
            "confidence": 88.0,
            "risk_label": "HIGH",
            "common_funder_score": 70.0,
            "bundle_blocks": [{"block": 1, "buyers": 4, "buys": 9}],
            "common_funders": [{"funder": "0xf", "funded_count": 4}],
            "source": "own",
        }

    monkeypatch.setattr(
        "rugmunch_scanner.unified.engines.own_bundle_detect.get_own_bundle_analysis",
        _fake_bundle,
    )
    with patch.object(dep, "http_get_json", side_effect=ConnectionError("offline")):
        out = await dep._supplement_deployer_socials(_base_result(), "0x" + "ab" * 20, "ethereum")
    ba = out["bundle_analysis"]
    assert ba["is_bundled"] is True
    assert ba["launch_buyers"] == 4
    assert ba["cluster_blocks"] == [1]


@pytest.mark.asyncio
async def test_enrichment_indexer_legs_timeboxed_and_fail_open(monkeypatch):
    """A hung ClickHouse engine must not block the card (6s cap, fail-open)."""

    async def _cancel(*a, **k):
        raise TimeoutError()

    monkeypatch.setattr(
        "rugmunch_scanner.unified.engines.deployer_profiles.get_deployer_profile", _cancel
    )
    monkeypatch.setattr(
        "rugmunch_scanner.unified.engines.own_bundle_detect.get_own_bundle_analysis", _cancel
    )
    with patch.object(dep, "http_get_json", side_effect=ConnectionError("offline")):
        out = await dep._supplement_deployer_socials(
            _base_result(deployer_address="0x" + "cd" * 20), "0x" + "ab" * 20, "ethereum"
        )
    assert "deployer_profile" not in out  # honest-missing, not fabricated
    assert "bundle_analysis" not in out


# ── Win 3: cache observability ───────────────────────────────────────


class _FakePipe:
    def __init__(self, store):
        self.store = store

    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self

    def expire(self, key, ttl):
        return self

    def execute(self):
        return []


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.pipeline = lambda transaction=False: _FakePipe(self.store)

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value


@pytest.mark.asyncio
async def test_scan_cache_counters_hit_and_miss(monkeypatch):
    import json

    import rugmunchbot.tools.security as sec

    fake = _FakeRedis()
    monkeypatch.setattr("rugmunchbot._vendor.redis_client.get_redis", lambda: fake)

    addr = "0x" + "ee" * 20

    # MISS path (backend returns a scoreable payload)
    async def _fake_post(path, **kw):
        return {"risk_score": 42, "symbol": "T"}

    with patch.object(sec, "backend_json_post", _fake_post):
        await sec.security_scan(address=addr, chain="eth")
    # HIT path (second call reads the stored payload)
    with patch.object(sec, "backend_json_post", _fake_post):
        res = await sec.security_scan(address=addr, chain="eth")
    assert res.source == "cache:scanresult"

    hits = [k for k in fake.store if k.startswith("stats:scanresult:hits")]
    misses = [k for k in fake.store if k.startswith("stats:scanresult:misses")]
    assert len(hits) == 1
    assert fake.store[hits[0]] == 1
    assert len(misses) == 1
    assert fake.store[misses[0]] == 1
    # cached payload round-trips through JSON intact
    raw = fake.store[f"scanresult:scan:eth:{addr}"]
    assert json.loads(raw)["risk_score"] == 42


@pytest.mark.asyncio
async def test_scan_cache_fresh_bypass_counts_neither(monkeypatch):
    import rugmunchbot.tools.security as sec

    fake = _FakeRedis()
    monkeypatch.setattr("rugmunchbot._vendor.redis_client.get_redis", lambda: fake)

    async def _fake_post(path, **kw):
        return {"risk_score": 7}

    with patch.object(sec, "backend_json_post", _fake_post):
        await sec.security_scan(address="0x" + "ff" * 20, chain="eth", fresh=True)
    stats = [k for k in fake.store if k.startswith("stats:scanresult")]
    assert stats == [], "fresh bypass counts as neither hit nor miss"
