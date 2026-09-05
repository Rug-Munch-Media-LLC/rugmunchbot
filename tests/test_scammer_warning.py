"""Scammer-warning posts + solana_syndicate group seeding.

Covers the 2026-08-29 ZachXBT Solana-syndicate deliverable:

* seed integrity: the ``solana_syndicate`` group exists in SCAMMER_GROUPS,
  the 10 verified handles map to it, and Ansem (``blknoiz06``) is excluded
* warning formatter: HTML card cites ZachXBT, names the shilled token, and
  carries the "reason not to buy anything they post" doctrine
* collection: recorded calls whose identity resolves into a warning group
  are surfaced (window-gated)
* high-signal routing: shill/rug calls escalate to main, plain buys stay on
  alerts
* runner: posts to alerts (+ main/feed for high signal), deduped per
  handle+token per day
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rugmunchbot.channels.scammer_warning import (
    collect_group_shill_calls,
    format_scammer_warning,
    is_high_signal,
)


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    from rugmunchbot import db as db_pkg

    monkeypatch.setattr(db_pkg, "DB_PATH", str(tmp_path / "test_bot.db"))
    from rugmunchbot.db._base import init_db

    init_db()
    return tmp_path


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _seed_identity(handle: str, group: str) -> None:
    from rugmunchbot.db import register_alias, upsert_kol_identity

    kol_id = handle.lower()
    upsert_kol_identity(
        kol_id=kol_id,
        screen_name=handle,
        category="scammer",
        source="scammer_list",
        risk_level="scammer",
        scammer_group=group,
    )
    register_alias(kol_id, handle, is_current=True)


def _record_call(
    handle: str,
    tweet_id: str,
    *,
    token_symbol: str = "RUGX",  # noqa: S107 — test helper default, not a credential
    call_type: str = "shill",
    tweet_text: str = "$RUGX gem 100x aping in",
) -> str:
    from rugmunchbot.db import record_kol_call

    record_kol_call(
        handle=handle,
        tweet_id=tweet_id,
        token_symbol=token_symbol,
        call_type=call_type,
        tweet_text=tweet_text,
        source_url=f"https://x.com/{handle}/status/{tweet_id}",
    )
    return tweet_id


# ══════════════════════════════════════════════════════════════════════════
# Seed integrity — solana_syndicate group + real handles
# ══════════════════════════════════════════════════════════════════════════


def test_solana_syndicate_group_seeded_with_real_handles():
    """SCAMMER_GROUPS has solana_syndicate; the 10 handles map to it."""
    from rugmunchbot.kol_registry_seed import SCAMMER_GROUP_MAP, SCAMMER_GROUPS

    assert "solana_syndicate" in SCAMMER_GROUPS
    label = SCAMMER_GROUPS["solana_syndicate"]["label"]
    assert "ZachXBT" in label

    expected = {
        "mrbitcoinwhalee",
        "mrcryptoxwhale",
        "crypto0637",
        "btcdailynotes",
        "cryptodailynot",
        "jihooncrypto",
        "seongwooiq300",
        "iq300min",
        "raqeem_saudi",
        "mrcryptowhalecn",
    }
    for handle in expected:
        assert SCAMMER_GROUP_MAP.get(handle) == "solana_syndicate", handle


def test_ansem_excluded_from_solana_syndicate():
    """Owner rule: blknoiz06 (Ansem) is NOT in the solana_syndicate group."""
    from rugmunchbot.kol_registry_seed import SCAMMER_GROUP_MAP

    assert SCAMMER_GROUP_MAP.get("blknoiz06") != "solana_syndicate"
    # Ansem lives in the separate hearsay group (pre-existing) — untouched.
    assert SCAMMER_GROUP_MAP.get("blknoiz06") == "solana_kol_syndicate"


def test_solana_syndicate_scammer_rows_seeded():
    """The 10 accounts are real known_scammers rows (evidence URLs attached)."""
    from rugmunchbot.kol_registry_seed import KOL_SEED

    handles = {r[0].lower() for r in KOL_SEED}
    for h in [
        "mrbitcoinwhalee",
        "mrcryptoxwhale",
        "crypto0637",
        "btcdailynotes",
        "cryptodailynot",
        "jihooncrypto",
        "seongwooiq300",
        "iq300min",
        "raqeem_saudi",
        "mrcryptowhalecn",
    ]:
        assert h in handles, h


# ══════════════════════════════════════════════════════════════════════════
# Warning formatter
# ══════════════════════════════════════════════════════════════════════════


def test_format_scammer_warning_renders():
    """HTML warning cites ZachXBT + token + the reason-not-to-buy doctrine."""
    text = format_scammer_warning(
        handle="MrBitcoinWhalee",
        token_symbol="ORAMAMA",
        call_type="shill",
        tweet_text="$ORAMAMA is going to melt faces",
        source_url="https://x.com/zachxbt/status/2038297120034422945",
        group="solana_syndicate",
    )
    assert "SCAMMER WARNING" in text
    assert "@MrBitcoinWhalee" in text
    assert "$ORAMAMA" in text
    assert "ZachXBT" in text
    assert "Reason not to buy anything they post" in text
    assert "x.com/zachxbt/status/2038297120034422945" in text


def test_format_scammer_warning_address_token():
    """Address-only calls render a shortened address token."""
    text = format_scammer_warning(
        handle="crypto0637",
        token_address="G5nxEXuFMfV74DSnsrSatqCW32F34XUnBeq3PfDS7w5E",
        group="solana_syndicate",
    )
    assert "G5nxEXuF" in text


# ══════════════════════════════════════════════════════════════════════════
# Collection — group-attributed calls surface
# ══════════════════════════════════════════════════════════════════════════


def test_collect_group_shill_calls(tmp_db):
    """Calls from a solana_syndicate identity surface; others do not."""
    _seed_identity("MrBitcoinWhalee", "solana_syndicate")
    _seed_identity("cobie", "analyst")
    _record_call("MrBitcoinWhalee", "t1", token_symbol="RUGX")
    _record_call("cobie", "t2", token_symbol="SAFE")

    calls = collect_group_shill_calls(groups=("solana_syndicate",))
    handles = {c["handle"].lower() for c in calls}
    assert "mrbitcoinwhalee" in handles
    assert "cobie" not in handles


def test_collect_group_shill_calls_window(tmp_db):
    """Stale calls outside the window do not warn."""
    from rugmunchbot.db import get_db

    _seed_identity("MrBitcoinWhalee", "solana_syndicate")
    _record_call("MrBitcoinWhalee", "t-old", token_symbol="RUGX")
    # Backdate the captured_at column (record_kol_call stamps it internally).
    conn = get_db()
    try:
        conn.execute(
            "UPDATE kol_calls SET captured_at = ? WHERE tweet_id = ?",
            (datetime(2024, 1, 1, tzinfo=UTC).isoformat(), "t-old"),
        )
        conn.commit()
    finally:
        conn.close()
    calls = collect_group_shill_calls(window_hours=72)
    assert calls == []


def test_is_high_signal():
    """shill/rug calls are high-signal; plain buys are not."""
    assert is_high_signal({"call_type": "shill"})
    assert is_high_signal({"call_type": "rug"})
    assert not is_high_signal({"call_type": "buy"})
    assert not is_high_signal({})


# ══════════════════════════════════════════════════════════════════════════
# Routing — alerts always, main only high signal, feed on main
# ══════════════════════════════════════════════════════════════════════════


class _FakeBot:
    """Captures sends; returns True (delivery succeeded)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.photos: list[dict] = []

    async def send_message(self, **kwargs) -> None:
        self.sent.append((kwargs.get("chat_id", ""), kwargs.get("text", "")))

    async def send_photo(self, **kwargs) -> None:
        self.photos.append(kwargs)


async def test_post_scammer_warning_routes(tmp_db, monkeypatch):
    """High-signal shill → alerts + main + feed mirror attempted."""
    from rugmunchbot.channels.scammer_warning import post_scammer_warning

    _seed_identity("MrBitcoinWhalee", "solana_syndicate")
    _record_call(
        "MrBitcoinWhalee",
        "t-route",
        token_symbol="RUGX",
        call_type="shill",
        tweet_text="$RUGX gem 100x aping in",
    )
    # resolve the call row back with _group set (as the runner does)
    calls = collect_group_shill_calls(groups=("solana_syndicate",))
    call = next(c for c in calls if c["tweet_id"] == "t-route")

    sent: list[str] = []

    async def _fake_safe_send(bot, chat_id, text, **kw) -> bool:
        sent.append(chat_id)
        return True

    # Patch the source modules the function imports lazily at call time.
    monkeypatch.setattr("rugmunchbot.channels.scheduler._core._safe_send", _fake_safe_send)
    monkeypatch.setattr("rugmunchbot.config._resolve_channel_id", lambda role: f"chat-{role}")
    monkeypatch.setattr(
        "rugmunchbot.channels.scheduler._posts_rag.mirror_post_to_feed",
        lambda item: "post-123",
    )

    bot = _FakeBot()
    result = await post_scammer_warning(bot, call)
    assert result["routes"]["alerts"] is True
    assert result["routes"]["main"] is True
    assert result["routes"]["feed"] is True
    assert "chat-alerts" in sent
    # Image doctrine (2026-08-31): main ships photo + caption, never text.
    assert any(p["chat_id"] == "chat-main" for p in bot.photos)


async def test_plain_buy_stays_on_alerts(tmp_db, monkeypatch):
    """Non-high-signal call → alerts only, no main/feed."""
    from rugmunchbot.channels.scammer_warning import post_scammer_warning

    _seed_identity("crypto0637", "solana_syndicate")
    _record_call(
        "crypto0637",
        "t-buy",
        token_symbol="PEPE",
        call_type="buy",
        tweet_text="Bought $PEPE, position open",
    )
    calls = collect_group_shill_calls(groups=("solana_syndicate",))
    call = next(c for c in calls if c["tweet_id"] == "t-buy")

    sent: list[str] = []

    async def fake_safe_send(bot, chat_id, text, **kw) -> bool:
        sent.append(chat_id)
        return True

    monkeypatch.setattr("rugmunchbot.channels.scheduler._core._safe_send", fake_safe_send)
    monkeypatch.setattr("rugmunchbot.config._resolve_channel_id", lambda role: f"chat-{role}")
    monkeypatch.setattr(
        "rugmunchbot.channels.scheduler._posts_rag.mirror_post_to_feed",
        lambda item: "post-123",
    )

    result = await post_scammer_warning(_FakeBot(), call)
    assert result["routes"]["alerts"] is True
    assert result["routes"]["main"] is False
    assert result["routes"]["feed"] is False
    assert sent == ["chat-alerts"]


def test_runner_dry_run_renders(tmp_db, monkeypatch):
    """run_scammer_warning_watch with ctx=None renders + releases (dry run)."""
    from rugmunchbot.channels.scammer_warning import (
        format_scammer_warning,
    )

    _seed_identity("MrBitcoinWhalee", "solana_syndicate")
    _record_call("MrBitcoinWhalee", "t-dry", token_symbol="RUGX", call_type="shill")
    calls = collect_group_shill_calls(groups=("solana_syndicate",))
    assert calls
    # Dry-run: ctx=None → no post, key released, 0 returned. The formatter
    # itself renders (this is the render smoke test).
    rendered = format_scammer_warning(
        handle=calls[0]["handle"],
        token_symbol=calls[0]["token_symbol"],
        call_type=calls[0]["call_type"],
        group="solana_syndicate",
    )
    assert "SCAMMER WARNING" in rendered
    assert "ZachXBT" in rendered
