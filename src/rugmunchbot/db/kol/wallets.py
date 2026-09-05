#!/usr/bin/env python3
"""Wallet knowledgebase: traced wallets, inference links, clustering."""

from __future__ import annotations

import json
from typing import Any

from .._base import get_db
from ._helpers import json_list, norm_handle, now_iso, wallet_entries
from .identity import get_kol_identity, register_alias, upsert_kol_identity


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
    wallets = wallet_entries(row.get("wallets"))
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
        conn.execute(
            "UPDATE kol_identities SET wallets = ?, updated_at = ? WHERE kol_id = ?",
            (json.dumps(wallets, separators=(",", ":")), now_iso(), norm_handle(kol_id)),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def link_identity_via_wallet(kol_id_a: str, kol_id_b: str) -> bool:
    """Inference link: same wallet surfaced under two identities → link them.

    Marks both identities as linked (``linked_kol_ids``) so the knowledge
    base records the wallet-inference connection. Does NOT merge rows —
    a human reviews the merge.
    """
    a = norm_handle(kol_id_a)
    b = norm_handle(kol_id_b)
    if not a or not b or a == b:
        return False
    conn = get_db()
    try:
        for kol_id, other in ((a, b), (b, a)):
            row = conn.execute(
                "SELECT linked_kol_ids FROM kol_identities WHERE kol_id = ?", (kol_id,)
            ).fetchone()
            linked = json_list(row["linked_kol_ids"] if row else None)
            if other not in linked:
                linked.append(other)
                conn.execute(
                    "UPDATE kol_identities SET linked_kol_ids = ?, updated_at = ? WHERE kol_id = ?",
                    (json.dumps(linked, separators=(",", ":")), now_iso(), kol_id),
                )
        conn.commit()
        return True
    finally:
        conn.close()


def mark_kol_verified(kol_id: str) -> bool:
    """Flip the verified flag (ApiPass user-tweets confirmed the handle)."""
    kol_id = norm_handle(kol_id)
    if not kol_id:
        return False
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE kol_identities SET verified = 1, updated_at = ? WHERE kol_id = ?",
            (now_iso(), kol_id),
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
            if any(w.get("address", "").lower() == wallet for w in wallet_entries(r["wallets"]))
        ]
    finally:
        conn.close()


def list_kol_wallets(kol_id: str) -> list[dict[str, str]]:
    """Traced wallets for one KOL: address + chain + confidence (+ provenance).

    This is the wallet-tracing render accessor — each traced wallet carries
    its chain and confidence so the scoreboard can show
    ``addr… (chain, confidence)``. Unknown kol_id → [].
    """
    row = get_kol_identity(kol_id)
    if row is None:
        return []
    return wallet_entries(row.get("wallets"))


def wallet_kb_by_source(source: str, limit: int = 500) -> list[dict[str, Any]]:
    """Wallet-KB reverse lookup by provenance source tag.

    Every traced wallet stores its source (``github``, ``gmgn_kol``,
    ``kolscan``, ``etherscan``, or an operator-seeded evidence source such
    as ``zachxbt_post`` / ``dethective``). Returns identities + the wallet
    entries that match the source tag. No addresses are fabricated here —
    the tag comes from the data.
    """
    source = str(source or "").strip().lower()
    if not source:
        return []
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM kol_identities").fetchall()
    finally:
        conn.close()
    matches: list[dict[str, Any]] = []
    for r in rows:
        ident = dict(r)
        for w in wallet_entries(ident.get("wallets")):
            if w.get("source", "").lower() == source:
                matches.append(
                    {
                        "kol_id": ident["kol_id"],
                        "screen_name": ident["screen_name"],
                        "address": w["address"],
                        "chain": w.get("chain", ""),
                        "confidence": w.get("confidence", ""),
                        "evidence_url": w.get("evidence_url", ""),
                    }
                )
                if len(matches) >= max(1, limit):
                    return matches
    return matches


def wallet_clusters() -> list[dict[str, Any]]:
    """On-chain clustering: identities linked by a SHARED traced wallet.

    Same wallet under two identities is the wallet-inference link signal.
    Returns one cluster per shared wallet with member identities
    (sorted by evidence strength: deterministic > strong_heuristic >
    probabilistic > inference). Fail-open ``[]``.
    """
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM kol_identities").fetchall()
    finally:
        conn.close()
    by_wallet: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        ident = dict(r)
        for w in wallet_entries(ident.get("wallets")):
            by_wallet.setdefault(w.get("address", "").lower(), []).append(
                {
                    "identity": ident,
                    "chain": w.get("chain", ""),
                    "confidence": w.get("confidence", ""),
                }
            )
    _strength = {"deterministic": 0, "strong_heuristic": 1, "probabilistic": 2, "inference": 3}
    clusters: list[dict[str, Any]] = []
    for addr, members in by_wallet.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: _strength.get(m["confidence"], 9))
        clusters.append(
            {
                "wallet": addr,
                "chain": next((m["chain"] for m in members if m["chain"]), ""),
                "member_count": len(members),
                "members": [
                    {
                        "kol_id": m["identity"]["kol_id"],
                        "screen_name": m["identity"]["screen_name"],
                        "confidence": m["confidence"],
                    }
                    for m in members
                ],
            }
        )
    clusters.sort(key=lambda c: (-c["member_count"], c["wallet"]))
    return clusters


def cluster_kols_by_wallet(limit: int = 50) -> int:
    """Run the wallet-inference clustering pass over the whole KB.

    For every wallet shared by ≥2 identities, links the pairs via
    :func:`link_identity_via_wallet` so ``linked_kol_ids`` records the
    inference connection (no rows merged — a human reviews merges).
    Returns link records written.
    """
    linked = 0
    for cluster in wallet_clusters()[: max(1, limit)]:
        members = [m["kol_id"] for m in cluster["members"]]
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                if link_identity_via_wallet(members[i], members[j]):
                    linked += 1
    return linked


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
        kol_id = norm_handle(handle)
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
