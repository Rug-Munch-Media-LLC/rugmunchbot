#!/usr/bin/env python3
"""Canonical KOL identities, handle aliases, name changes, syndicate groups."""

from __future__ import annotations

import json
from typing import Any

from .._base import get_db
from ._helpers import norm_handle, now_iso


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
    scammer_group: str = "",
    notes: str = "",
) -> bool:
    """Insert/update one canonical KOL identity (upsert by ``kol_id``).

    ``kol_id`` is the canonical slug (normally the current lowercase handle);
    ``screen_name`` is the current display handle. Every handle a KOL has
    ever used must be registered as an alias via :func:`register_alias` so
    past/current names resolve to this identity. ``scammer_group`` is the
    syndicate slug (stable id; labels live in the seed registry) — empty
    means "not group-attributed".
    """
    kol_id = norm_handle(kol_id)
    screen_name = str(screen_name or "").strip().lstrip("@") or kol_id
    if not kol_id:
        return False
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO kol_identities (
                kol_id, name, screen_name, category, source, verified,
                risk_level, wallets, linked_kol_ids, scammer_group, notes,
                added_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                scammer_group = CASE WHEN excluded.scammer_group = ''
                                     THEN kol_identities.scammer_group
                                     ELSE excluded.scammer_group END,
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
                str(scammer_group or ""),
                str(notes or ""),
                now_iso(),
                now_iso(),
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
            "SELECT * FROM kol_identities WHERE kol_id = ?", (norm_handle(kol_id),)
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def list_kol_identities(
    *,
    category: str = "",
    risk_level: str = "",
    scammer_group: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Registered identities, optional category / risk_level / group filters."""
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
        if scammer_group:
            where.append("scammer_group = ?")
            params.append(str(scammer_group))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY screen_name ASC LIMIT ?"
        params.append(max(1, limit))
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def kol_identities_by_group(scammer_group: str, limit: int = 500) -> list[dict[str, Any]]:
    """Every identity attributed to one syndicate (empty list when none)."""
    return list_kol_identities(scammer_group=scammer_group, limit=limit)


def list_kol_groups() -> list[dict[str, Any]]:
    """Syndicate summary: group slug + member count (group-attributed only)."""
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT scammer_group, COUNT(*) AS member_count,
                   GROUP_CONCAT(screen_name, ', ') AS handles
            FROM kol_identities
            WHERE scammer_group != ''
            GROUP BY scammer_group
            ORDER BY member_count DESC, scammer_group ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def seed_scammer_groups(group_map: dict[str, str]) -> int:
    """Attach ``handle -> group_slug`` attributions to registered identities.

    ``group_map`` keys are handles (any case); values are stable group slugs
    (labels live in the seed registry / caller). Idempotent — re-running with
    the same map overwrites the same slugs. Returns identities updated.
    """
    count = 0
    for handle, group in (group_map or {}).items():
        kol_id = norm_handle(handle)
        group = str(group or "").strip()
        if not kol_id or not group:
            continue
        conn = get_db()
        try:
            cur = conn.execute(
                "UPDATE kol_identities SET scammer_group = ?, updated_at = ? WHERE kol_id = ?",
                (group, now_iso(), kol_id),
            )
            conn.commit()
            count += cur.rowcount
        finally:
            conn.close()
    return count


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
    kol_id = norm_handle(kol_id)
    alias = norm_handle(alias)
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
            (alias, kol_id, 1 if is_current else 0, now_iso(), now_iso()),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def demote_alias(alias: str) -> bool:
    """Mark one handle as no-longer-current (it was renamed away from)."""
    alias = norm_handle(alias)
    if not alias:
        return False
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE kol_aliases SET is_current = 0, last_seen = ? WHERE alias = ?",
            (now_iso(), alias),
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
    alias = norm_handle(handle)
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
    kol_id = norm_handle(kol_id)
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
                norm_handle(kol_id),
                norm_handle(from_handle),
                norm_handle(to_handle),
                now_iso(),
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
                (norm_handle(kol_id), max(1, limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM kol_name_changes ORDER BY detected_at DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_kol_aliases(kol_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Historical alias list for one KOL: every handle ever resolved to it.

    Name-change tracking companion — the aliases table is the source of
    truth for "this KOL used to be @X". Each row carries ``is_current`` so a
    report card can distinguish the live handle from past ones. Sorted with
    the current alias first, then most-recently-seen.
    """
    kol_id = norm_handle(kol_id)
    if not kol_id:
        return []
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM kol_aliases WHERE kol_id = ? "
            "ORDER BY is_current DESC, last_seen DESC LIMIT ?",
            (kol_id, max(1, limit)),
        ).fetchall()
        return [dict(r) for r in rows]
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
        kol_id = norm_handle(handle)
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
