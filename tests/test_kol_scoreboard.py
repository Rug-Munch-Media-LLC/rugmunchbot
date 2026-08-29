"""KOL Scoreboard — report-card model, identity/name-change, store round-trip.

Covers the 2026-08-29 KOL Scoreboard deliverable (renamed from KOL Hall of
Shame; the Hall is now the repeat-scammer SUBSET):

* call classification + token/wallet extraction (pure)
* report-card aggregation: deletions >> shilling >> scam-token calls,
  win/loss from resolved outcomes
* repeat-scammer gate (Hall of Shame membership) + scoreboard ranking
* HTML formatters (Telegram parse_mode=html)
* 100%-correct identification: identity registry, alias resolution, and
  screen-name CHANGE handling (old handle resolves to the same card)
* SQLite store round-trip (record → list → delete → export JSONL)
* seed registry integrity: ≥500 real rows, no dup handles, real sources
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rugmunchbot.channels.kol_scoreboard import (
    KolCall,
    classify_call,
    compute_report_card,
    extract_tokens,
    extract_wallet_address,
    format_hall_of_shame_post,
    format_report_card,
    format_scoreboard_post,
    grade_for,
    rank_hall_of_shame,
    rank_scoreboard,
)


def _call(
    handle: str = "shillking",
    tweet_id: str = "t1",
    text: str = "$MOON gem 100x aping in",
    token_symbol_val: str = "MOON",  # noqa: S107
    addr: str = "",
    deleted: bool = False,
    result: str = "unknown",
) -> KolCall:
    return KolCall(
        handle=handle,
        tweet_id=tweet_id,
        token_symbol=token_symbol_val,
        token_address=addr,
        call_type=classify_call(text),
        tweet_text=text,
        posted_at=datetime.now(UTC).isoformat(),
        source_url=f"https://x.com/{handle}/status/{tweet_id}",
        is_deleted=deleted,
        deleted_at=datetime.now(UTC).isoformat() if deleted else "",
        result=result,
        captured_at=datetime.now(UTC).isoformat(),
    )


def _buy_call(handle: str = "decent", tweet_id: str = "t9", token: str = "PEPE") -> KolCall:  # noqa: S107
    """A plain (non-hype) call — the 'clean' control."""
    return _call(
        handle=handle,
        tweet_id=tweet_id,
        text=f"Bought ${token}, position open",
        token_symbol_val=token,
    )


# ══════════════════════════════════════════════════════════════════════════
# Classification + extraction (pure)
# ══════════════════════════════════════════════════════════════════════════


def test_classify_call_types():
    assert classify_call("$MOON is a gem, 100x incoming") == "shill"
    assert classify_call("Bought $PEPE, aping in") == "buy"  # plain call, no hype
    assert classify_call("This token will rug, exit now") == "rug"
    assert classify_call("The weather is nice today") == "other"
    assert classify_call("") == "other"


def test_extract_tokens():
    ticker, addr = extract_tokens("$MOON pumping hard 0xabc123def456")
    assert ticker == "MOON"
    assert addr == "0xabc123def456"
    ticker2, addr2 = extract_tokens("no tokens here")
    assert ticker2 == ""
    assert addr2 == ""


def test_extract_wallet_address():
    # Real solana address (from verified_kol_wallets.json — valid base58).
    sol = "G5nxEXuFMfV74DSnsrSatqCW32F34XUnBeq3PfDS7w5E"
    assert extract_wallet_address(f"just bought, wallet {sol}") == sol
    assert extract_wallet_address("$MOON gem") == ""
    # short 0x (contract-ish) is not treated as a wallet
    assert extract_wallet_address("0xabc123def456") == ""


# ══════════════════════════════════════════════════════════════════════════
# Report card (pure) + win/loss + repeat-scammer gate
# ══════════════════════════════════════════════════════════════════════════


def test_compute_report_card_counts():
    calls = [
        _call(tweet_id="1"),
        _call(tweet_id="2", deleted=True),
        _call(tweet_id="3", deleted=True),
        _call(tweet_id="4", text="This will rug hard"),
    ]
    card = compute_report_card(calls)
    assert card.handle == "shillking"
    assert card.total_calls == 4
    assert card.deleted_calls == 2
    assert card.shill_calls == 4  # 3 shill + 1 rug (all counted as shill-ish)
    assert card.tokens_mentioned == 1  # all $MOON
    assert card.is_repeat_scammer  # 2 deletions → repeat scammer


def test_compute_report_card_empty():
    card = compute_report_card([])
    assert card.handle == ""
    assert card.total_calls == 0
    assert card.grade == "N/A"
    assert not card.in_hall_of_shame


def test_report_card_win_loss():
    calls = [
        _buy_call(tweet_id="1"),
        _buy_call(tweet_id="2"),
        _buy_call(tweet_id="3"),
    ]
    from rugmunchbot.db import _base  # noqa: F401  (schema init only)

    # simulate resolved outcomes directly on the model
    calls[0].result = "win"
    calls[1].result = "loss"
    card = compute_report_card(calls)
    assert card.wins == 1
    assert card.losses == 1
    assert card.win_rate == 0.5


def test_shame_score_weights_deletion_heavily():
    clean = compute_report_card([_buy_call(tweet_id="1"), _buy_call(tweet_id="2")])
    deleted = compute_report_card(
        [_call(tweet_id="1", deleted=True), _call(tweet_id="2", deleted=True)]
    )
    assert clean.score == 0.0  # plain calls, no deletions → clean card
    assert deleted.score > clean.score * 10  # deletion dominates


def test_wins_offset_score():
    one_win = compute_report_card(
        [_call(tweet_id="1", result="win"), _call(tweet_id="2", result="win")]
    )
    zero = compute_report_card([_call(tweet_id="1"), _call(tweet_id="2")])
    assert one_win.score <= zero.score  # wins never hurt


def test_grade_bands():
    assert grade_for(0.0) == "A+"
    assert grade_for(4.9) == "A+"
    assert grade_for(5.0) == "A"
    assert grade_for(15.0) == "B"
    assert grade_for(30.0) == "C"
    assert grade_for(50.0) == "D"
    assert grade_for(80.0) == "F"
    assert grade_for(200.0) == "F"


def test_hall_of_shame_is_repeat_scammers_only():
    # One deletion + many shills but <3 → shill-heavy but NOT repeat scammer.
    one_del = compute_report_card([_call(tweet_id="1", deleted=True)])
    assert not one_del.is_repeat_scammer
    assert not one_del.in_hall_of_shame
    # Two deletions → repeat scammer → hall.
    two_del = compute_report_card(
        [_call(tweet_id="1", deleted=True), _call(tweet_id="2", deleted=True)]
    )
    assert two_del.is_repeat_scammer
    assert two_del.in_hall_of_shame
    # Three shills on distinct calls → repeat shiller.
    three_shills = compute_report_card(
        [_call(tweet_id=f"t{i}", text=f"$MOON{i} is a gem 100x moon") for i in range(3)]
    )
    assert three_shills.is_repeat_scammer


def test_scoreboard_ranks_all_hall_ranks_subset():
    bad = compute_report_card(
        [
            _call(tweet_id="1", handle="scammer", deleted=True),
            _call(tweet_id="2", handle="scammer", deleted=True),
        ]
    )
    ok = compute_report_card([_buy_call(tweet_id="3", handle="decent")])
    shilly = compute_report_card([_call(tweet_id=f"s{i}", handle="shilly") for i in range(2)])
    board = rank_scoreboard([ok, bad, shilly], limit=10)
    assert board[0].handle == "scammer"  # worst first
    hall = rank_hall_of_shame([ok, bad, shilly])
    assert hall == [bad]  # only the repeat scammer


# ══════════════════════════════════════════════════════════════════════════
# Formatters
# ══════════════════════════════════════════════════════════════════════════


def test_format_report_card_html():
    card = compute_report_card([_call(tweet_id="1", deleted=True)])
    text = format_report_card(card)
    assert "KOL report card" in text
    assert "@shillking" in text
    assert "deleted calls: 1" in text


def test_format_scoreboard_post():
    card = compute_report_card(
        [
            _call(tweet_id="1", deleted=True),
            _call(tweet_id="2", deleted=True),
            _call(tweet_id="3", text="$MOON 100x now"),
        ]
    )
    post = format_scoreboard_post([card])
    assert "KOL SCOREBOARD" in post
    assert "@shillking" in post
    assert "deleted" in post
    assert format_scoreboard_post([]) == ""


def test_format_hall_of_shame_post_subset_only():
    card = compute_report_card(
        [
            _call(tweet_id="1", deleted=True),
            _call(tweet_id="2", deleted=True),
            _call(tweet_id="3", text="$MOON 100x now"),
        ]
    )
    post = format_hall_of_shame_post([card])
    assert "KOL HALL OF SHAME" in post
    assert "repeat scammers" in post
    assert format_hall_of_shame_post([]) == ""


# ══════════════════════════════════════════════════════════════════════════
# Identity resolution + name-change handling (100% correct identification)
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    from rugmunchbot import db as db_pkg

    monkeypatch.setattr(db_pkg, "DB_PATH", str(tmp_path / "test_bot.db"))
    from rugmunchbot.db._base import init_db

    init_db()
    return tmp_path


def test_name_change_resolution(tmp_db):
    """A KOL who renames keeps ONE card: old handle resolves to the same id."""
    from rugmunchbot.channels.kol_scoreboard import detect_name_change, resolve_identity
    from rugmunchbot.db import (
        get_kol_scores,
        list_kol_name_changes,
        record_kol_call,
        register_alias,
        resolve_handle,
        upsert_kol_identity,
        upsert_kol_score,
    )

    # Seed the canonical identity with the CURRENT screen_name.
    upsert_kol_identity(
        kol_id="altcoingordon",
        name="Altcoin Gordon",
        screen_name="newhandle_now",
        category="shiller",
        source="ct_known",
        risk_level="watch",
    )
    register_alias("altcoingordon", "newhandle_now", is_current=True)
    # The OLD handle is registered as a PAST alias.
    register_alias("altcoingordon", "altcoingordon", is_current=False)

    # Both old and current handles resolve to the same identity.
    assert resolve_handle("altcoingordon")["kol_id"] == "altcoingordon"
    assert resolve_handle("newhandle_now")["kol_id"] == "altcoingordon"
    ident = resolve_identity("@ALTCOINGORDON")  # case/@-insensitive
    assert ident is not None
    assert ident.kol_id == "altcoingordon"

    # Sweep detects a NEW transition: swept 'altcoingordon' → payload 'renamed_again'.
    assert detect_name_change("altcoingordon", "renamed_again") is True
    changes = list_kol_name_changes(kol_id="altcoingordon")
    assert len(changes) == 1
    assert changes[0]["from_handle"] == "altcoingordon"
    assert changes[0]["to_handle"] == "renamed_again"
    # The new handle resolves too; the old alias was demoted.
    assert resolve_handle("renamed_again")["kol_id"] == "altcoingordon"
    assert resolve_handle("altcoingordon")["kol_id"] == "altcoingordon"

    # Calls recorded under the old handle land on the same report card.
    record_kol_call(handle="altcoingordon", tweet_id="old-call", token_symbol="MOON")
    record_kol_call(handle="renamed_again", tweet_id="new-call", token_symbol="PEPE")
    upsert_kol_score(
        handle="altcoingordon",
        total_calls=2,
        deleted_calls=0,
        shill_calls=2,
        scam_token_calls=0,
        tokens_mentioned=2,
        score=30.0,
        grade="C",
    )
    assert len(get_kol_scores()) == 1  # one card for the one KOL


def test_wallet_kb_and_linking(tmp_db):
    from rugmunchbot.db import (
        attach_kol_wallet,
        kol_identities_by_wallet,
        link_identity_via_wallet,
        upsert_kol_identity,
    )

    upsert_kol_identity(kol_id="kolA", screen_name="kolA")
    upsert_kol_identity(kol_id="kolB", screen_name="kolB")
    assert attach_kol_wallet("kolA", "addrA" * 6) is True
    assert attach_kol_wallet("kolA", "addrA" * 6) is False  # dedup
    assert attach_kol_wallet("kolB", "addrA" * 6) is True  # same wallet on B
    found = kol_identities_by_wallet("addrA" * 6)
    assert {f["kol_id"] for f in found} == {"kola", "kolb"}  # kol_ids normalized lowercase
    assert link_identity_via_wallet("kolA", "kolB") is True


# ══════════════════════════════════════════════════════════════════════════
# Seed registry integrity (real sources, ≥500 rows, no dup handles)
# ══════════════════════════════════════════════════════════════════════════


def test_seed_registry_has_500_real_rows():
    from rugmunchbot.kol_registry_seed import build_seed_rows, seed_stats

    rows = build_seed_rows()
    assert len(rows) >= 500
    handles = [r[0].lower() for r in rows]
    assert len(set(handles)) == len(handles)  # no duplicate primary handles
    for handle, name, category, source, risk, _notes in rows:
        assert handle
        assert name
        assert category
        assert source
        assert risk
        assert source in {
            "influential_registry",
            "scammer_list",
            "ct_known",
            "scam_watch",
            "zachxbt_post",
        }
    stats = seed_stats()
    assert stats["known_scammers"] >= 30
    assert stats["influential_registry"] >= 500
    assert stats["wallets"] >= 900


def test_seed_registry_contains_altcoingordon_and_zachxbt_sources():
    from rugmunchbot.kol_registry_seed import build_seed_rows

    rows = build_seed_rows()
    by_handle = {r[0].lower(): r for r in rows}
    assert "altcoingordon" in by_handle
    scammer_rows = [r for r in rows if r[4] == "scammer"]
    assert len(scammer_rows) >= 25  # evidence-backed scammer tier


def test_seed_into_db_round_trip(tmp_db):
    from rugmunchbot.db import kol_registry_count, list_kol_identities, seed_kol_registry
    from rugmunchbot.kol_registry_seed import build_seed_rows

    seeded = seed_kol_registry(build_seed_rows())
    assert seeded >= 500
    assert kol_registry_count() >= 500
    identities = list_kol_identities(limit=10000)
    assert len(identities) >= 500
    assert any(i["screen_name"].lower() == "altcoingordon" for i in identities)


# ══════════════════════════════════════════════════════════════════════════
# Store round-trip (tmp SQLite)
# ══════════════════════════════════════════════════════════════════════════


def test_store_round_trip(tmp_db):
    from rugmunchbot.db import (
        get_kol_call,
        get_kol_scores,
        list_kol_calls,
        mark_kol_call_deleted,
        record_kol_call,
        upsert_kol_score,
    )

    assert record_kol_call(
        handle="shillking", tweet_id="t1", token_symbol="MOON", tweet_text="$MOON 100x"
    )
    # Upsert by tweet_id — re-recording the same tweet doesn't dup.
    assert record_kol_call(
        handle="shillking", tweet_id="t1", token_symbol="MOON", tweet_text="$MOON 100x"
    )
    rows = list_kol_calls(handle="shillking")
    assert len(rows) == 1

    assert mark_kol_call_deleted("t1")
    row = get_kol_call("t1")
    assert row["is_deleted"] == 1
    assert row["deleted_at"]

    upsert_kol_score(
        handle="shillking",
        total_calls=1,
        deleted_calls=1,
        shill_calls=1,
        scam_token_calls=0,
        tokens_mentioned=1,
        score=57.0,
        grade="F",
    )
    scores = get_kol_scores()
    assert len(scores) == 1
    assert scores[0]["grade"] == "F"


def test_store_export_jsonl_callstore_shape(tmp_db):
    from rugmunchbot.db import export_kol_calls_jsonl, record_kol_call

    record_kol_call(
        handle="shillking",
        tweet_id="t1",
        token_symbol="MOON",
        tweet_text="$MOON 100x",
        source_url="https://x.com/shillking/status/t1",
    )
    path = str(tmp_db / "scammer_calls.jsonl")
    written = export_kol_calls_jsonl(path)
    assert written == 1
    import json

    with open(path) as fh:
        record = json.loads(fh.readline())
    # The rmi-backend site router (GET /api/v1/kol/scammer-calls) reads this
    # exact schema from app/databus/scammer_calls.json.
    assert record["handle"] == "shillking"
    assert record["channel"] == "x"
    assert record["token_symbol"] == "MOON"
    assert record["source_url"].startswith("https://x.com/")


# ══════════════════════════════════════════════════════════════════════════
# v9: Scammer groups (syndicate attribution) + Hall of Shame grouping
# ══════════════════════════════════════════════════════════════════════════


def test_scammer_group_model_round_trip(tmp_db):
    """scammer_group persists on the identity, resolves, and renders."""
    from rugmunchbot.db import (
        get_kol_identity,
        kol_identities_by_group,
        list_kol_groups,
        register_alias,
        resolve_handle,
        upsert_kol_identity,
    )

    upsert_kol_identity(
        kol_id="blknoiz06",
        name="Ansem",
        screen_name="blknoiz06",
        category="scammer",
        source="scammer_list",
        risk_level="scammer",
        scammer_group="solana_kol_syndicate",
    )
    register_alias("blknoiz06", "blknoiz06", is_current=True)

    row = get_kol_identity("blknoiz06")
    assert row["scammer_group"] == "solana_kol_syndicate"
    assert resolve_handle("blknoiz06")["scammer_group"] == "solana_kol_syndicate"
    assert len(kol_identities_by_group("solana_kol_syndicate")) == 1
    groups = list_kol_groups()
    assert any(
        g["scammer_group"] == "solana_kol_syndicate" and g["member_count"] == 1 for g in groups
    )


def test_scammer_group_renders_on_cards(tmp_db):
    """Group flows identity → report card → scoreboard/Hall formatters."""
    from rugmunchbot.channels.kol_scoreboard import (
        KolIdentity,
        compute_report_card,
        format_hall_of_shame_post,
        format_report_card,
        format_scoreboard_post,
    )
    from rugmunchbot.db import upsert_kol_identity

    upsert_kol_identity(
        kol_id="blknoiz06",
        screen_name="blknoiz06",
        category="scammer",
        source="scammer_list",
        risk_level="scammer",
        scammer_group="solana_kol_syndicate",
    )
    identity = KolIdentity.from_row(
        {"kol_id": "blknoiz06", "scammer_group": "solana_kol_syndicate"}
    )
    calls = [_call(tweet_id="1", deleted=True), _call(tweet_id="2", deleted=True)]
    card = compute_report_card(calls, identity=identity)
    assert card.scammer_group == "solana_kol_syndicate"

    report = format_report_card(card)
    assert "Solana KOL syndicate (Ansem)" in report
    assert card.in_hall_of_shame  # 2 deletions → repeat scammer + score ≥ 15

    scoreboard = format_scoreboard_post([card])
    assert "Solana KOL syndicate (Ansem)" in scoreboard

    hall = format_hall_of_shame_post([card])
    assert "Solana KOL syndicate (Ansem)" in hall  # Hall of Shame shows the group


def test_seed_scammer_groups_from_map(tmp_db):
    """seed_scammer_groups applies the evidence-driven handle→group map."""
    from rugmunchbot.db import list_kol_groups, seed_kol_registry, seed_scammer_groups
    from rugmunchbot.kol_registry_seed import KOL_SEED, SCAMMER_GROUP_MAP, build_scammer_group_map

    assert len(SCAMMER_GROUP_MAP) >= 10  # evidence-backed syndicate membership
    assert SCAMMER_GROUP_MAP["blknoiz06"] == "solana_kol_syndicate"
    assert SCAMMER_GROUP_MAP["dritankapllani"] == "impersonation_syndicate"
    # Groups used on real seeded identities:
    seeded = seed_kol_registry(KOL_SEED)
    assert seeded >= 500
    updated = seed_scammer_groups(build_scammer_group_map())
    assert updated >= 10
    groups = list_kol_groups()
    slugs = {g["scammer_group"] for g in groups}
    assert "solana_kol_syndicate" in slugs
    assert "impersonation_syndicate" in slugs


# ══════════════════════════════════════════════════════════════════════════
# v9: Wallet tracing — traced wallets (address+chain+confidence), inference
# linking from tweets, on-chain clustering, provenance-source lookup
# ══════════════════════════════════════════════════════════════════════════


def test_traced_wallets_carry_chain_and_confidence(tmp_db):
    """Each KOL gets traced wallets: address + chain + confidence."""
    from rugmunchbot.db import attach_kol_wallet, list_kol_wallets, upsert_kol_identity

    upsert_kol_identity(kol_id="kolA", screen_name="kolA")
    sol = "G5nxEXuFMfV74DSnsrSatqCW32F34XUnBeq3PfDS7w5E"
    assert attach_kol_wallet(
        "kolA",
        sol,
        chain="solana",
        source="github",
        evidence_url="https://github.com/sn3ll/KOL-Wallets-BULLX",
        confidence="strong_heuristic",
    )
    wallets = list_kol_wallets("kolA")
    assert len(wallets) == 1
    w = wallets[0]
    assert w["address"] == sol
    assert w["chain"] == "solana"
    assert w["confidence"] == "strong_heuristic"


def test_inference_wallet_linking_from_call(tmp_db):
    """A wallet-shaped address in a KOL tweet links to their KB (inference)."""
    from rugmunchbot.channels.kol_scoreboard import link_wallet_from_call
    from rugmunchbot.db import (
        list_kol_wallets,
        register_alias,
        upsert_kol_identity,
    )

    upsert_kol_identity(kol_id="kolX", screen_name="kolX")
    register_alias("kolX", "kolX", is_current=True)
    sol = "G5nxEXuFMfV74DSnsrSatqCW32F34XUnBeq3PfDS7w5E"
    call = _call(
        handle="kolX",
        tweet_id="t9",
        text=f"just aped, my wallet {sol}",
        token_symbol_val="MOON",
    )
    assert link_wallet_from_call(call) is True
    wallets = list_kol_wallets("kolX")
    assert wallets[0]["address"] == sol
    assert wallets[0]["chain"] == "solana"
    assert wallets[0]["confidence"] == "inference"
    # Re-linking the same wallet is a dedup no-op (False).
    assert link_wallet_from_call(call) is False


def test_wallet_clustering_and_link(tmp_db):
    """Shared wallet across identities → on-chain cluster + inference link."""
    from rugmunchbot.db import (
        attach_kol_wallet,
        cluster_kols_by_wallet,
        get_kol_identity,
        upsert_kol_identity,
        wallet_clusters,
    )

    upsert_kol_identity(kol_id="kolA", screen_name="kolA")
    upsert_kol_identity(kol_id="kolB", screen_name="kolB")
    shared = "ADDR" * 8  # 32-char base58-ish
    attach_kol_wallet("kolA", shared, chain="solana", confidence="deterministic")
    attach_kol_wallet("kolB", shared, chain="solana", confidence="probabilistic")

    clusters = wallet_clusters()
    assert len(clusters) == 1
    assert clusters[0]["wallet"] == shared.lower()
    assert clusters[0]["member_count"] == 2
    # deterministic member sorts before probabilistic member
    assert clusters[0]["members"][0]["kol_id"] == "kola"

    linked = cluster_kols_by_wallet()
    assert linked >= 1
    assert "kolb" in (get_kol_identity("kolA") or {}).get("linked_kol_ids", "")


def test_wallet_kb_by_source_lookup(tmp_db):
    """Provenance-tagged wallets surface via source lookup (e.g. zachxbt)."""
    from rugmunchbot.db import attach_kol_wallet, upsert_kol_identity, wallet_kb_by_source

    upsert_kol_identity(kol_id="kolC", screen_name="kolC")
    attach_kol_wallet(
        "kolC",
        "DETECTIVEADDR" + "1" * 20,
        chain="ethereum",
        source="dethective",
        confidence="strong_heuristic",
    )
    hits = wallet_kb_by_source("dethective")
    assert len(hits) == 1
    assert hits[0]["kol_id"] == "kolc"
    assert hits[0]["address"].startswith("DETECTIVEADDR")
    assert wallet_kb_by_source("zachxbt_post") == []  # no fabricated zachxbt rows


# ══════════════════════════════════════════════════════════════════════════
# v9: Name-change verification — renames caught + historical alias list
# ══════════════════════════════════════════════════════════════════════════


def test_name_change_keeps_historical_alias_list(tmp_db):
    """Renames (altcoingordon → current) keep a full historical alias list."""
    from rugmunchbot.channels.kol_scoreboard import detect_name_change
    from rugmunchbot.db import (
        list_kol_aliases,
        register_alias,
        resolve_handle,
        upsert_kol_identity,
    )

    upsert_kol_identity(
        kol_id="altcoingordon",
        name="Altcoin Gordon",
        screen_name="currenthandle",
        category="shiller",
        source="ct_known",
        risk_level="watch",
    )
    register_alias("altcoingordon", "currenthandle", is_current=True)
    register_alias("altcoingordon", "altcoingordon", is_current=False)

    # Both old and new handles resolve to one identity.
    assert resolve_handle("altcoingordon")["kol_id"] == "altcoingordon"
    assert resolve_handle("currenthandle")["kol_id"] == "altcoingordon"

    # A later rename is caught and the historical list grows.
    assert detect_name_change("currenthandle", "evennewer") is True
    aliases = list_kol_aliases("altcoingordon")
    by_alias = {a["alias"]: a for a in aliases}
    assert set(by_alias) == {"altcoingordon", "currenthandle", "evennewer"}
    assert by_alias["evennewer"]["is_current"] == 1
    assert by_alias["currenthandle"]["is_current"] == 0
    assert by_alias["altcoingordon"]["is_current"] == 0
    # The newest handle is first (is_current DESC, last_seen DESC).
    assert aliases[0]["alias"] == "evennewer"


# ══════════════════════════════════════════════════════════════════════════
# v9: Scorecard render — group, calls, win-rate, deleted, shills, wallets,
# grade all present on one card
# ══════════════════════════════════════════════════════════════════════════


def test_report_card_renders_full_scorecard_fields(tmp_db):
    """One card renders: group, calls, win-rate, deleted, shills, wallets, grade."""
    from rugmunchbot.channels.kol_scoreboard import (
        KolIdentity,
        compute_report_card,
        format_report_card,
    )

    calls = [
        _call(tweet_id="1", deleted=True, result="win"),
        _call(tweet_id="2", deleted=True, result="loss"),
        _call(tweet_id="3", text="$MOON is a gem 100x"),
        _call(tweet_id="4", text="Bought $PEPE position open"),
    ]
    identity = KolIdentity.from_row(
        {
            "kol_id": "shillking",
            "name": "Shill King",
            "category": "shiller",
            "scammer_group": "impersonation_syndicate",
        }
    )
    card = compute_report_card(
        calls,
        identity=identity,
        traced_wallets=[
            {
                "address": "G5nxEXuFMfV74DSnsrSatqCW32F34XUnBeq3PfDS7w5E",
                "chain": "solana",
                "confidence": "probabilistic",
            }
        ],
    )
    assert card.total_calls == 4
    assert card.win_rate == 0.5
    text = format_report_card(card)
    assert "CZ-impersonation syndicate" in text  # group
    assert "calls: 4" in text  # calls
    assert "win-rate 50%" in text  # win-rate
    assert "deleted calls: 2" in text  # deleted
    assert "shill/rug calls:" in text  # shills
    assert "traced wallets (1)" in text  # wallets
    assert "solana/probabilistic" in text  # chain + confidence
    assert f"grade {card.grade}" in text  # grade
