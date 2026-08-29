#!/usr/bin/env python3
"""KOL Hall-of-Shame DB accessors — calls, deletions, cached report cards.

Persists the X/CT KOL call records the ApiPass pipeline captures
(``channels.kol_scoreboard``) into the bot SQLite store:

* ``kol_calls`` — one row per captured call (handle, tweet, token, deleted
  state). ``tweet_id`` is unique so re-sweeping the same tweet upserts.
* ``kol_scores`` — cached per-handle report-card aggregates so the site /
  alerts reads never re-aggregate the whole table.

All functions are sync sqlite3 accessors (the channel runner wraps them via
``db.run_db`` / ``get_db_async`` when called from the event loop). Pure
scoring logic lives in ``channels.kol_scoreboard`` — this module only
stores and fetches rows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ._base import get_db


def _now() -> str:
    return datetime.now(UTC).isoformat()


def record_kol_call(
    *,
    handle: str,
    tweet_id: str,
    token_symbol: str = "",
    token_address: str = "",
    chain: str = "",
    call_type: str = "buy",
    tweet_text: str = "",
    posted_at: str = "",
    source_url: str = "",
    wallet_address: str = "",
) -> bool:
    """Insert one KOL call; upserts by ``tweet_id`` (re-sweeps don't dup)."""
    handle = str(handle or "").strip().lstrip("@").lower()
    tweet_id = str(tweet_id or "").strip()
    if not handle or not tweet_id:
        return False
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO kol_calls (
                handle, tweet_id, token_symbol, token_address, chain, call_type,
                tweet_text, posted_at, source_url, wallet_address, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tweet_id) DO UPDATE SET
                handle = excluded.handle,
                token_symbol = COALESCE(NULLIF(excluded.token_symbol, ''), kol_calls.token_symbol),
                token_address = COALESCE(NULLIF(excluded.token_address, ''), kol_calls.token_address),
                chain = COALESCE(NULLIF(excluded.chain, ''), kol_calls.chain),
                call_type = excluded.call_type,
                tweet_text = COALESCE(NULLIF(excluded.tweet_text, ''), kol_calls.tweet_text),
                source_url = COALESCE(NULLIF(excluded.source_url, ''), kol_calls.source_url),
                wallet_address = COALESCE(NULLIF(excluded.wallet_address, ''), kol_calls.wallet_address)
            """,
            (
                handle,
                tweet_id,
                token_symbol,
                token_address,
                chain,
                call_type,
                tweet_text,
                posted_at,
                source_url,
                wallet_address,
                _now(),
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def mark_kol_call_deleted(tweet_id: str) -> bool:
    """Mark one call as deleted (retracted) with the current timestamp."""
    tweet_id = str(tweet_id or "").strip()
    if not tweet_id:
        return False
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE kol_calls SET is_deleted = 1, deleted_at = ? WHERE tweet_id = ?",
            (_now(), tweet_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_kol_call(tweet_id: str) -> dict[str, Any] | None:
    """One call row by tweet id (None when unknown)."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM kol_calls WHERE tweet_id = ?", (str(tweet_id or ""),)
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def list_kol_calls(handle: str = "", limit: int = 200) -> list[dict[str, Any]]:
    """Recent calls, optionally scoped to one handle (newest captured first)."""
    conn = get_db()
    try:
        if handle:
            rows = conn.execute(
                "SELECT * FROM kol_calls WHERE handle = ? ORDER BY captured_at DESC LIMIT ?",
                (str(handle).strip().lstrip("@").lower(), max(1, limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM kol_calls ORDER BY captured_at DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def upsert_kol_score(
    *,
    handle: str,
    total_calls: int,
    deleted_calls: int,
    shill_calls: int,
    scam_token_calls: int,
    tokens_mentioned: int,
    score: float,
    grade: str,
) -> None:
    """Cache one handle's aggregated report card."""
    handle = str(handle or "").strip().lstrip("@").lower()
    if not handle:
        return
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO kol_scores (
                handle, total_calls, deleted_calls, shill_calls, scam_token_calls,
                tokens_mentioned, score, grade, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(handle) DO UPDATE SET
                total_calls = excluded.total_calls,
                deleted_calls = excluded.deleted_calls,
                shill_calls = excluded.shill_calls,
                scam_token_calls = excluded.scam_token_calls,
                tokens_mentioned = excluded.tokens_mentioned,
                score = excluded.score,
                grade = excluded.grade,
                updated_at = excluded.updated_at
            """,
            (
                handle,
                int(total_calls),
                int(deleted_calls),
                int(shill_calls),
                int(scam_token_calls),
                int(tokens_mentioned),
                float(score),
                str(grade or "N/A"),
                _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_kol_scores(limit: int = 100) -> list[dict[str, Any]]:
    """Cached report cards, worst first (highest shame score on top)."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM kol_scores ORDER BY score DESC, deleted_calls DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def export_kol_calls_jsonl(path: str, limit: int = 500) -> int:
    """Write recent calls as JSON Lines in the backend ``CallStore`` shape.

    The rmi-backend site router (``GET /api/v1/kol/scammer-calls``) reads
    exactly this schema from ``app/databus/scammer_calls.json``; an operator
    can point that store at this export (or copy it) so the KOL hall of
    shame renders on the site. Returns rows written (0 on failure).
    """
    import json

    try:
        calls = list_kol_calls(limit=limit)
        with open(path, "w", encoding="utf-8") as fh:
            for c in calls:
                record = {
                    "id": f"kol-call-{c.get('tweet_id') or c.get('id')}",
                    "handle": c.get("handle"),
                    "channel": "x",
                    "post_text": (c.get("tweet_text") or "")[:500],
                    "token_symbol": c.get("token_symbol") or None,
                    "token_address": c.get("token_address") or None,
                    "chain": c.get("chain") or None,
                    "severity": "critical" if (c.get("is_deleted") or 0) else "medium",
                    "captured_at": c.get("captured_at"),
                    "source_url": c.get("source_url") or None,
                }
                fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        return len(calls)
    except OSError:
        return 0


# ══════════════════════════════════════════════════════════════════════════
# KOL SCOREBOARD v8 — canonical identities, handle aliases, name changes,
# wallet knowledgebase, ApiPass credit meter
# ══════════════════════════════════════════════════════════════════════════


def _norm_handle(handle: str) -> str:
    return str(handle or "").strip().lstrip("@").lower()


def _json_list(value: Any) -> list[str]:
    import json

    if isinstance(value, list):
        return [str(v) for v in value]
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
        return [str(v) for v in parsed] if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def upsert_kol_identity(
    *,
    kol_id: str,
    name: str = "",
    screen_name: str = "",
    category: str = "shiller",
    source: str = "ct_known",
    verified: bool = False,
    risk_level: str = "watch",
    wallets: list[str] | None = None,
    linked_kol_ids: list[str] | None = None,
    notes: str = "",
) -> bool:
    """Insert/update one canonical KOL identity (upsert by ``kol_id``).

    ``kol_id`` is the canonical slug (normally the current lowercase handle);
    ``screen_name`` is the current display handle. Every handle a KOL has
    ever used must be registered as an alias via :func:`register_alias` so
    past/current names resolve to this identity.
    """
    import json

    kol_id = _norm_handle(kol_id)
    screen_name = str(screen_name or "").strip().lstrip("@") or kol_id
    if not kol_id:
        return False
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO kol_identities (
                kol_id, name, screen_name, category, source, verified,
                risk_level, wallets, linked_kol_ids, notes, added_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(kol_id) DO UPDATE SET
                name = COALESCE(NULLIF(excluded.name, ''), kol_identities.name),
                screen_name = COALESCE(
                    NULLIF(excluded.screen_name, ''), kol_identities.screen_name),
                category = COALESCE(NULLIF(excluded.category, ''), kol_identities.category),
                source = COALESCE(NULLIF(excluded.source, ''), kol_identities.source),
                verified = excluded.verified,
                risk_level = COALESCE(
                    NULLIF(excluded.risk_level, ''), kol_identities.risk_level),
                wallets = CASE WHEN excluded.wallets = '[]' THEN kol_identities.wallets
                               ELSE excluded.wallets END,
                linked_kol_ids = CASE WHEN excluded.linked_kol_ids = '[]'
                                      THEN kol_identities.linked_kol_ids
                                      ELSE excluded.linked_kol_ids END,
                notes = CASE WHEN excluded.notes = '' THEN kol_identities.notes
                             ELSE excluded.notes END,
                updated_at = excluded.updated_at
            """,
            (
                kol_id,
                str(name or ""),
                screen_name,
                str(category or "shiller"),
                str(source or "ct_known"),
                1 if verified else 0,
                str(risk_level or "watch"),
                json.dumps(wallets or [], separators=(",", ":")),
                json.dumps(linked_kol_ids or [], separators=(",", ":")),
                str(notes or ""),
                _now(),
                _now(),
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_kol_identity(kol_id: str) -> dict[str, Any] | None:
    """One canonical identity by kol_id (None when unknown)."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM kol_identities WHERE kol_id = ?", (_norm_handle(kol_id),)
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def list_kol_identities(
    *, category: str = "", risk_level: str = "", limit: int = 500
) -> list[dict[str, Any]]:
    """Registered identities, optional category / risk_level filters."""
    conn = get_db()
    try:
        sql = "SELECT * FROM kol_identities"
        where: list[str] = []
        params: list[Any] = []
        if category:
            where.append("category = ?")
            params.append(str(category))
        if risk_level:
            where.append("risk_level = ?")
            params.append(str(risk_level))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY screen_name ASC LIMIT ?"
        params.append(max(1, limit))
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def kol_registry_count() -> int:
    """Number of canonical KOL identities seeded/tracked."""
    conn = get_db()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM kol_identities").fetchone()
        return int(row["n"] or 0) if row else 0
    finally:
        conn.close()


def register_alias(kol_id: str, alias: str, *, is_current: bool = True) -> bool:
    """Map one handle (current or past) to a canonical kol_id.

    This is the table that makes screen-name changes resolve: a KOL who
    renames keeps their old handle as an alias (``is_current=False``) so
    both names resolve to the same report card.
    """
    kol_id = _norm_handle(kol_id)
    alias = _norm_handle(alias)
    if not kol_id or not alias:
        return False
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO kol_aliases (alias, kol_id, is_current, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(alias) DO UPDATE SET
                kol_id = excluded.kol_id,
                is_current = excluded.is_current,
                last_seen = excluded.last_seen
            """,
            (alias, kol_id, 1 if is_current else 0, _now(), _now()),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def demote_alias(alias: str) -> bool:
    """Mark one handle as no-longer-current (it was renamed away from)."""
    alias = _norm_handle(alias)
    if not alias:
        return False
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE kol_aliases SET is_current = 0, last_seen = ? WHERE alias = ?",
            (_now(), alias),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def resolve_handle(handle: str) -> dict[str, Any] | None:
    """Resolve ANY handle (current or past) to its canonical identity.

    Returns the identity row (with ``wallets``/``linked_kol_ids`` still as
    JSON strings) or None. Case-insensitive, strips ``@``. This is the
    100%-identification core: old and new screen names land on one card.
    """
    alias = _norm_handle(handle)
    if not alias:
        return None
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT i.* FROM kol_aliases a
            JOIN kol_identities i ON a.kol_id = i.kol_id
            WHERE a.alias = ?
            """,
            (alias,),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def record_kol_name_change(
    kol_id: str, *, from_handle: str, to_handle: str, source: str = "apipass_sweep"
) -> bool:
    """Record one detected screen-name change (old handle → new handle)."""
    kol_id = _norm_handle(kol_id)
    if not kol_id or not from_handle or not to_handle:
        return False
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO kol_name_changes (kol_id, from_handle, to_handle, detected_at, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                _norm_handle(kol_id),
                _norm_handle(from_handle),
                _norm_handle(to_handle),
                _now(),
                source,
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def list_kol_name_changes(kol_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """Recent name-change events, optionally scoped to one KOL."""
    conn = get_db()
    try:
        if kol_id:
            rows = conn.execute(
                "SELECT * FROM kol_name_changes WHERE kol_id = ? ORDER BY detected_at DESC LIMIT ?",
                (_norm_handle(kol_id), max(1, limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM kol_name_changes ORDER BY detected_at DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def attach_kol_wallet(
    kol_id: str,
    wallet: str,
    *,
    chain: str = "",
    source: str = "inference",
    evidence_url: str = "",
    confidence: str = "",
) -> bool:
    """Add one traced wallet to a KOL identity (dedup, never removes).

    Wallet knowledgebase: wallets come from on-chain tracing (zachxbt-style
    inference), a call tweet containing a raw address, or curated fleet data
    (``verified_kol_wallets.json``). Entries are stored as dicts so
    provenance (chain/source/evidence) survives. Returns True when the
    wallet was added (False when already known or no identity).
    """
    wallet = str(wallet or "").strip()
    if not wallet:
        return False
    row = get_kol_identity(kol_id)
    if row is None:
        return False
    wallets = _wallet_entries(row.get("wallets"))
    if any(w.get("address", "").lower() == wallet.lower() for w in wallets):
        return False
    entry: dict[str, str] = {
        "address": wallet,
        "chain": str(chain or ""),
        "source": str(source or "inference"),
        "evidence_url": str(evidence_url or ""),
        "confidence": str(confidence or ""),
    }
    wallets.append(entry)
    conn = get_db()
    try:
        import json

        conn.execute(
            "UPDATE kol_identities SET wallets = ?, updated_at = ? WHERE kol_id = ?",
            (json.dumps(wallets, separators=(",", ":")), _now(), _norm_handle(kol_id)),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _wallet_entries(value: Any) -> list[dict[str, str]]:
    """Coerce the wallets column (JSON list of dicts, legacy bare strings)."""
    import json

    if isinstance(value, list):
        raw = value
    elif not value:
        return []
    else:
        try:
            raw = json.loads(str(value))
        except (ValueError, TypeError):
            return []
        if not isinstance(raw, list):
            return []
    out: list[dict[str, str]] = []
    for v in raw:
        if isinstance(v, dict):
            out.append(
                {
                    "address": str(v.get("address") or ""),
                    "chain": str(v.get("chain") or ""),
                    "source": str(v.get("source") or ""),
                    "evidence_url": str(v.get("evidence_url") or ""),
                    "confidence": str(v.get("confidence") or ""),
                }
            )
        else:
            out.append(
                {
                    "address": str(v),
                    "chain": "",
                    "source": "legacy",
                    "evidence_url": "",
                    "confidence": "",
                }
            )
    return out


def link_identity_via_wallet(kol_id_a: str, kol_id_b: str) -> bool:
    """Inference link: same wallet surfaced under two identities → link them.

    Marks both identities as linked (``linked_kol_ids``) so the knowledge
    base records the wallet-inference connection. Does NOT merge rows —
    a human reviews the merge.
    """
    a = _norm_handle(kol_id_a)
    b = _norm_handle(kol_id_b)
    if not a or not b or a == b:
        return False
    conn = get_db()
    try:
        import json

        for kol_id, other in ((a, b), (b, a)):
            row = conn.execute(
                "SELECT linked_kol_ids FROM kol_identities WHERE kol_id = ?", (kol_id,)
            ).fetchone()
            linked = _json_list(row["linked_kol_ids"] if row else None)
            if other not in linked:
                linked.append(other)
                conn.execute(
                    "UPDATE kol_identities SET linked_kol_ids = ?, updated_at = ? WHERE kol_id = ?",
                    (json.dumps(linked, separators=(",", ":")), _now(), kol_id),
                )
        conn.commit()
        return True
    finally:
        conn.close()


def mark_kol_verified(kol_id: str) -> bool:
    """Flip the verified flag (ApiPass user-tweets confirmed the handle)."""
    kol_id = _norm_handle(kol_id)
    if not kol_id:
        return False
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE kol_identities SET verified = 1, updated_at = ? WHERE kol_id = ?",
            (_now(), kol_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def kol_identities_by_wallet(wallet: str) -> list[dict[str, Any]]:
    """Identities known to hold *wallet* (wallet-KB reverse lookup)."""
    wallet = str(wallet or "").strip().lower()
    if not wallet:
        return []
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM kol_identities").fetchall()
        return [
            dict(r)
            for r in rows
            if any(w.get("address", "").lower() == wallet for w in _wallet_entries(r["wallets"]))
        ]
    finally:
        conn.close()


def seed_wallet_kb(entries: list[dict[str, str]]) -> int:
    """Seed the wallet knowledgebase from real traced-wallet data.

    Each entry: ``{handle, name, chain, address, source, evidence_url,
    confidence}``. Creates a ``wallet_kb`` identity when the handle is not
    yet in the registry, then attaches the wallet with full provenance.
    Returns wallets attached.
    """
    count = 0
    for e in entries:
        handle = str(e.get("handle") or "").strip().lstrip("@")
        address = str(e.get("address") or "").strip()
        if not handle or not address:
            continue
        kol_id = _norm_handle(handle)
        if get_kol_identity(kol_id) is None:
            upsert_kol_identity(
                kol_id=kol_id,
                name=str(e.get("name") or "").strip() or handle,
                screen_name=handle,
                category="shiller",
                source=str(e.get("source") or "wallet_kb"),
                verified=False,
                risk_level="watch",
                notes="wallet KB: " + (str(e.get("confidence") or "")),
            )
            register_alias(kol_id, handle, is_current=True)
        if attach_kol_wallet(
            kol_id,
            address,
            chain=str(e.get("chain") or ""),
            source=str(e.get("source") or "wallet_kb"),
            evidence_url=str(e.get("evidence_url") or ""),
            confidence=str(e.get("confidence") or ""),
        ):
            count += 1
    return count


def seed_kol_rug_history(rows: list[dict[str, str]]) -> int:
    """Seed documented call outcomes (rug/dump history) as loss calls.

    Each row: ``{handle, token_symbol, chain, date, outcome, evidence_url,
    notes}``. The tweet_id is a deterministic storage key derived from the
    evidence URL (never a fabricated tweet); result is always ``loss`` for
    the fleet's documented dumped/rugged outcomes. Returns rows recorded.
    """
    import hashlib

    count = 0
    for r in rows:
        handle = str(r.get("handle") or "").strip().lstrip("@")
        token = str(r.get("token_symbol") or "").strip()
        url = str(r.get("evidence_url") or "").strip()
        date = str(r.get("date") or "").strip()
        if not handle or not token:
            continue
        key_src = f"{handle}:{token}:{date}:{url or ''}"
        tweet_id = "rug-" + hashlib.sha256(key_src.encode()).hexdigest()[:16]
        text = f"Called ${token} ({date}) — documented loss outcome."
        if url:
            text += f" Evidence: {url}"
        recorded = record_kol_call(
            handle=handle,
            tweet_id=tweet_id,
            token_symbol=token,
            chain=str(r.get("chain") or ""),
            call_type="buy",
            tweet_text=text[:500],
            posted_at=date,
            source_url=url,
        )
        if recorded:
            update_kol_call_result(tweet_id, "loss")
            count += 1
    return count


def update_kol_call_result(tweet_id: str, result: str) -> bool:
    """Best-effort outcome for one call: win | loss | flat | unknown."""
    result = str(result or "").strip().lower()
    if result not in {"win", "loss", "flat", "unknown"}:
        return False
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE kol_calls SET result = ? WHERE tweet_id = ? AND result = 'unknown'",
            (result, str(tweet_id or "").strip()),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def record_apipass_usage(
    *,
    endpoint: str = "",
    model: str = "",
    credits: float = 0.5,
    task_id: str = "",
    status: str = "created",
) -> bool:
    """Meter one ApiPass task creation (the ≤3 credits/hr budget ledger)."""
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO kol_apipass_usage (ts, endpoint, model, credits, task_id, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                _now(),
                str(endpoint or ""),
                str(model or ""),
                float(credits),
                str(task_id or ""),
                str(status),
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def apipass_usage_summary() -> dict[str, Any]:
    """Credit-burn dashboard: calls, credits used, remaining of the free 50.

    ``RMI_APIPASS_FREE_CREDITS`` (default 50) is the free allowance; each
    task creation meters ``RMI_APIPASS_CREDITS_PER_CALL`` (default 0.5).
    """
    import os

    free_credits = float(os.getenv("RMI_APIPASS_FREE_CREDITS", "50"))
    credits_per_call = float(os.getenv("RMI_APIPASS_CREDITS_PER_CALL", "0.5"))
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM kol_apipass_usage").fetchone()
        credits_row = conn.execute(
            "SELECT COALESCE(SUM(credits), 0.0) AS c FROM kol_apipass_usage WHERE status = 'created'"
        ).fetchone()
        today = _now()[:10]
        today_row = conn.execute(
            "SELECT COUNT(*) AS n FROM kol_apipass_usage WHERE ts LIKE ?", (today + "%",)
        ).fetchone()
        endpoints = conn.execute(
            "SELECT endpoint, COUNT(*) AS n, COALESCE(SUM(credits), 0.0) AS credits "
            "FROM kol_apipass_usage GROUP BY endpoint ORDER BY credits DESC"
        ).fetchall()
        credits_used = float(credits_row["c"] or 0.0) if credits_row else 0.0
        return {
            "total_calls": int(total["n"] or 0) if total else 0,
            "credits_used": round(credits_used, 2),
            "free_allowance": free_credits,
            "credits_remaining": round(max(0.0, free_credits - credits_used), 2),
            "calls_today": int(today_row["n"] or 0) if today_row else 0,
            "calls_per_hour_budget": int(os.getenv("RMI_APIPASS_MAX_CALLS_PER_HOUR", "6")),
            "credits_per_call": credits_per_call,
            "estimated_calls_remaining": int((free_credits - credits_used) / credits_per_call),
            "by_endpoint": [
                {
                    "endpoint": str(e["endpoint"]),
                    "calls": int(e["n"]),
                    "credits": round(float(e["credits"]), 2),
                }
                for e in endpoints
            ],
        }
    finally:
        conn.close()


def seed_kol_registry(rows: list[tuple[str, str, str, str, str, str]]) -> int:
    """Seed/refresh the canonical KOL registry from curated rows.

    Each row: ``(handle, name, category, source, risk_level, notes)``.
    Idempotent: identities upsert, primary alias registered, verified flag
    is preserved (seeding never un-verifies). Returns rows processed.
    """
    count = 0
    for handle, name, category, source, risk_level, notes in rows:
        handle = str(handle or "").strip().lstrip("@")
        if not handle:
            continue
        kol_id = _norm_handle(handle)
        existing = get_kol_identity(kol_id)
        upsert_kol_identity(
            kol_id=kol_id,
            name=str(name or "").strip() or handle,
            screen_name=handle,
            category=str(category or "shiller"),
            source=str(source or "ct_known"),
            verified=bool(existing and existing.get("verified")),
            risk_level=str(risk_level or "watch"),
            notes=str(notes or ""),
        )
        register_alias(kol_id, handle, is_current=True)
        count += 1
    return count


__all__ = [
    "apipass_usage_summary",
    "attach_kol_wallet",
    "demote_alias",
    "export_kol_calls_jsonl",
    "get_kol_call",
    "get_kol_identity",
    "get_kol_scores",
    "kol_identities_by_wallet",
    "kol_registry_count",
    "link_identity_via_wallet",
    "list_kol_calls",
    "list_kol_identities",
    "list_kol_name_changes",
    "mark_kol_call_deleted",
    "mark_kol_verified",
    "record_apipass_usage",
    "record_kol_call",
    "record_kol_name_change",
    "register_alias",
    "resolve_handle",
    "seed_kol_registry",
    "seed_kol_rug_history",
    "seed_wallet_kb",
    "update_kol_call_result",
    "upsert_kol_identity",
    "upsert_kol_score",
]
