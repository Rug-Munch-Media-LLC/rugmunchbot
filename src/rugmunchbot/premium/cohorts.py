"""Cohort rollup — team, insiders and snipers as three comparable rows.

A single "top holders" table hides the only question that matters: *which*
group is selling. Labelling wallets into cohorts and reporting each cohort
with the same five numbers (how many, how many are bundled, what they bought
initially, what they hold now, what they realised) makes the comparison
mechanical instead of interpretive.

Every number is derived from data already present in the scanner payload or
the label store. When a cohort has no members the row is zeros with
``known=False`` — an empty cohort and a cohort we could not see are different
facts, and the caller is told which one it is.
"""

from __future__ import annotations

from typing import Any

COHORTS = ("team", "insiders", "snipers")
#: First-buyer rank at or below which a wallet is treated as a sniper.
SNIPER_MAX_RANK = 5


def _row(members: list[dict[str, Any]], known: bool) -> dict[str, Any]:
    return {
        "count": len(members),
        "bundled_n": sum(1 for member in members if member.get("bundled")),
        "initial_buy_pct": round(sum(float(m.get("initial_pct") or 0.0) for m in members), 4),
        "current_pct": round(sum(float(m.get("current_pct") or 0.0) for m in members), 4),
        "realized_sol": round(sum(float(m.get("realized_sol") or 0.0) for m in members), 4),
        "known": known,
    }


def _classify(
    wallet: str,
    labels: dict[str, Any],
    first_buyers: dict[str, dict[str, Any]],
) -> str | None:
    """Cohort for one wallet: explicit label first, behaviour second."""
    label = labels.get(wallet) or {}
    cohort = str(label.get("cohort") or "").lower()
    if cohort in COHORTS:
        return cohort
    if label.get("is_team") or label.get("role") == "team":
        return "team"
    if label.get("is_insider") or cohort in ("insider", "dev", "deployer"):
        return "insiders"
    buyer = first_buyers.get(wallet) or {}
    rank = buyer.get("rank")
    if isinstance(rank, int) and rank <= SNIPER_MAX_RANK:
        return "snipers"
    if buyer.get("sniper") or label.get("is_sniper"):
        return "snipers"
    return None


def cohort_rollup(
    holders: list[dict[str, Any]],
    first_buyers: list[dict[str, Any]] | None = None,
    labels: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Roll holders and first buyers into the three cohorts plus KOLs.

    Args:
        holders: ``{"wallet"|"address", "pct", "realized_sol", "bundled"}`` rows.
        first_buyers: ``{"wallet", "rank"|"slot", "pct"|"initial_pct"}`` rows.
        labels: ``wallet -> {"cohort"|"is_team"|"is_insider"|"is_sniper",
            "handle", "x_url", "pnl_sol"}`` from the label/KOL store.

    Returns:
        ``{"cohorts": {team, insiders, snipers}, "kol": [...]}`` where each
        cohort row carries ``count``, ``bundled_n``, ``initial_buy_pct``,
        ``current_pct`` and ``realized_sol``.
    """
    label_map = labels or {}
    buyers = {
        str(b.get("wallet") or b.get("address") or ""): {
            "rank": b.get("rank") if isinstance(b.get("rank"), int) else b.get("slot"),
            "initial_pct": b.get("initial_pct")
            if b.get("initial_pct") is not None
            else b.get("pct"),
            "sniper": b.get("sniper"),
        }
        for b in (first_buyers or [])
        if b.get("wallet") or b.get("address")
    }

    members: dict[str, list[dict[str, Any]]] = {cohort: [] for cohort in COHORTS}
    for holder in holders:
        wallet = str(holder.get("wallet") or holder.get("address") or "")
        if not wallet:
            continue
        cohort = _classify(wallet, label_map, buyers)
        if cohort is None:
            continue
        buyer = buyers.get(wallet) or {}
        members[cohort].append(
            {
                "wallet": wallet,
                "bundled": bool(holder.get("bundled") or holder.get("in_bundle")),
                "initial_pct": buyer.get("initial_pct")
                if buyer.get("initial_pct") is not None
                else holder.get("initial_pct"),
                "current_pct": holder.get("pct")
                if holder.get("pct") is not None
                else holder.get("current_pct"),
                "realized_sol": holder.get("realized_sol"),
            }
        )

    kol = [
        {
            "handle": str(label.get("handle") or wallet),
            "x_url": label.get("x_url")
            or (f"https://x.com/{label['handle']}" if label.get("handle") else None),
            "pnl_sol": label.get("pnl_sol"),
        }
        for wallet, label in sorted(label_map.items())
        if isinstance(label, dict) and (label.get("handle") or label.get("cohort") == "kol")
    ]
    return {
        "cohorts": {
            cohort: _row(members[cohort], known=bool(members[cohort])) for cohort in COHORTS
        },
        "kol": kol,
    }
