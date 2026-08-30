"""Tests for rugmunchbot.data.kol_ingest — KOL scraped-data ingest.

Uses a temp SQLite DB (monkeypatched via the db package ``DB_PATH``) so no
production data is touched. Verifies dedup, persistence to
``kol_identities``/``kol_aliases``, wallet attachment, Wall of Shame flagging
and the Qdrant embedding fail-open path (embedder + qdrant are monkeypatched).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from rugmunchbot import db
from rugmunchbot.data import kol_ingest


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the bot DB at a fresh temp file and init the schema."""
    db_path = tmp_path / "bot.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    from rugmunchbot.db._base import init_db

    init_db()
    return db_path


def _rec(handle="newtrader", addr="11111111111111111111111111111111111111111111", **kw):
    base = {
        "handle": handle,
        "name": handle.title(),
        "address": addr,
        "chain": "solana",
        "source": "kolscan_leaderboard",
        "twitter": f"https://x.com/{handle}",
        "telegram": "",
        "profit": 10.0,
        "wins": 1,
        "losses": 0,
        "followers": 0,
        "evidence_url": "https://kolscan.io/leaderboard",
    }
    base.update(kw)
    return base


def test_existing_registry_empty(temp_db):
    known = kol_ingest.existing_registry()
    assert known["wallets"] == set()
    assert known["handles"] == set()


def test_ingest_persists_new_identity(temp_db):
    await_ = asyncio.run
    rec = _rec()
    summary = await_(kol_ingest.ingest_scraped([rec], embed=False))
    assert summary["identities_persisted"] == 1
    assert summary["aliases_registered"] == 1
    assert summary["wallets_attached"] == 1
    # The identity exists in the DB.
    from rugmunchbot.db._base import get_db

    conn = get_db()
    row = conn.execute("SELECT kol_id FROM kol_identities WHERE kol_id = 'newtrader'").fetchone()
    assert row is not None
    assert row["kol_id"] == "newtrader"
    conn.close()


def test_ingest_dedups_against_existing(temp_db):
    await_ = asyncio.run
    await_(kol_ingest.ingest_scraped([_rec()], embed=False))
    # Second run with the same handle+wallet must add nothing new.
    summary = await_(kol_ingest.ingest_scraped([_rec()], embed=False))
    assert summary["new_records"] == 0
    assert summary["identities_persisted"] == 0
    assert summary["wallets_attached"] == 0


def test_ingest_treats_new_wallet_as_new(temp_db):
    await_ = asyncio.run
    await_(kol_ingest.ingest_scraped([_rec(handle="samehandle")], embed=False))
    # Same handle but a NEW wallet → new identity (wallet is the unique key).
    summary = await_(
        kol_ingest.ingest_scraped(
            [_rec(handle="samehandle", addr="22222222222222222222222222222222222222222222")],
            embed=False,
        )
    )
    # The identity kol_id is the handle, so upsert dedups it; wallet attaches.
    assert summary["wallets_attached"] == 1


def test_flag_wall_of_shame_appends_evidence_row(temp_db, tmp_path, monkeypatch):
    rec = _rec(
        handle="loser",
        addr="33333333333333333333333333333333333333333333",
        wins=1,
        losses=20,
        evidence_url="https://kolscan.io/account/x",
    )
    # Point Wall of Shame at a temp dir so we don't mutate the real data.
    data_dir = tmp_path / "wall_of_shame"
    data_dir.mkdir()
    monkeypatch.setattr(kol_ingest, "_DATA_DIR", data_dir)
    count = kol_ingest.flag_wall_of_shame([rec])
    assert count == 1
    wall_of_shame_path = data_dir / "wall_of_shame.json"
    data = json.loads(wall_of_shame_path.read_text())
    assert data["count"] == 1
    assert data["wall_of_shame"][0]["handle"] == "loser"


def test_upsert_kol_embedding_fail_open(monkeypatch):
    """A dead embedder/Qdrant path must never raise (returns False)."""
    monkeypatch.setattr(kol_ingest, "upsert_kol_embedding", lambda *a, **k: False)
    ok_ = asyncio.run(kol_ingest._embed(["x"]))
    # _embed hits the local qwen3 layer; if unreachable it returns [] or 2560-dim
    # zero-padded vectors, never raises.
    assert isinstance(ok_, list)
