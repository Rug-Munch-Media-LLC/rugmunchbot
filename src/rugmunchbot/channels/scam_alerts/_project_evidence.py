"""Evidence + pusher normalisation for project-level scam alerts.

An accusation needs a sourced fact. Every item carries a recognised ``kind``,
a human-readable ``detail`` and a ``source`` (channel handle, URL, or on-chain
reference). Anything else is DROPPED — a scam alert never invents a flag, a
number, or an accuser.

Pure module: no network, no clock, no I/O.
"""

from __future__ import annotations

from typing import Any

#: Recognised evidence kinds → (proof weight, human label).
#: The weight measures *strength of proof*, never a market risk score.
EVIDENCE_KINDS: dict[str, tuple[int, str]] = {
    "honeypot": (40, "honeypot contract (sells blocked)"),
    "drainer": (40, "wallet drainer"),
    "deployer_rug_history": (35, "deployer has rug history"),
    "lp_pull": (35, "liquidity pulled"),
    "mint_authority": (30, "mint/freeze authority retained"),
    "concentration": (25, "supply concentrated in few wallets"),
    "bundled_launch": (25, "bundled launch / sniper wallets"),
    "fake_volume": (25, "wash trading / fake volume"),
    "paid_shill_campaign": (20, "paid promotion of the project"),
    "guaranteed_returns": (20, "'guaranteed returns' claims"),
    "unofficial_clone": (20, "impersonates a known project"),
    "social_impersonation": (15, "impersonation account / fake team"),
    "coordinated_pump": (15, "coordinated pump-group activity"),
}

#: Pusher kinds we can name — a channel, a wallet, or the deployer.
PUSHER_KINDS = frozenset({"channel", "wallet", "deployer", "account"})

MAX_EVIDENCE = 6
MAX_PUSHERS = 5


def normalise_evidence(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Keep only sourced, recognised evidence; dedupe identical claims.

    Returns up to :data:`MAX_EVIDENCE` items shaped
    ``{kind, label, detail, source, weight}``, strongest first.
    """
    kept: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "").strip().lower()
        detail = " ".join(str(raw.get("detail") or "").split())
        source = str(raw.get("source") or "").strip()
        if kind not in EVIDENCE_KINDS or not detail or not source:
            continue
        key = (kind, detail.lower(), source.lower())
        if key in seen:
            continue
        seen.add(key)
        weight, label = EVIDENCE_KINDS[kind]
        kept.append(
            {"kind": kind, "label": label, "detail": detail, "source": source, "weight": weight}
        )
    kept.sort(key=lambda item: item["weight"], reverse=True)
    return kept[:MAX_EVIDENCE]


def normalise_pushers(pushers: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Keep only named pushers carrying a handle/address AND a source.

    "Who is pushing it" is an attribution claim, so an entry without a
    ``source`` is discarded rather than published.
    """
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in pushers or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "").strip().lower()
        handle = str(raw.get("handle") or raw.get("address") or "").strip()
        source = str(raw.get("source") or "").strip()
        if kind not in PUSHER_KINDS or not handle or not source:
            continue
        identity = handle.lower()
        if identity in seen:
            continue
        seen.add(identity)
        kept.append(
            {
                "kind": kind,
                "handle": handle,
                "role": " ".join(str(raw.get("role") or "").split())[:120],
                "source": source,
            }
        )
    return kept[:MAX_PUSHERS]
