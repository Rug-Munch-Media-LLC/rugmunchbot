"""Fill the legacy top-level gaps from the v2 ``evidence`` block.

The fast scan path returns security answers NESTED —
``evidence.security.checks[id].details`` carries GoPlus results as text
(``"GoPlus honeypot=no"``, ``"Mintable=no"``, ``"Verified=NO"``) — while the
card (and the legacy report) read flat keys (``honeypot``, ``mintable``,
``contract_verified``). Without this adapter every safety field renders ``—``
on a token we actually know the answer for, which is exactly the failure the
card exists to fix (rule 5: we own the safety layer).

The adapter only FILLS gaps: a flat value already present always wins, and an
absent answer stays absent (``None``), so nothing is invented.

One public entry point: :func:`normalize`.
"""

from __future__ import annotations

import copy
import re
from typing import Any

#: Legacy flat key -> (evidence check id, label inside ``details``).
CHECKS: dict[str, tuple[str, str]] = {
    "honeypot": ("HP01", "honeypot"),
    "mintable": ("CR03", "mintable"),
    "freezable": ("TE04", "blacklist"),
    "contract_verified": ("CR01", "verified"),
}

#: ``key=value`` pairs inside a check's human-readable detail string.
PAIR_RE = re.compile(r"([A-Za-z_ ]+?)=([^\s,;]+)")

TRUTHY = frozenset({"yes", "true", "1", "y"})
FALSEY = frozenset({"no", "false", "0", "n"})


def _as_bool(raw: str) -> bool | None:
    token = raw.strip().strip(".").lower()
    if token in TRUTHY:
        return True
    if token in FALSEY:
        return False
    return None


def _details(evidence: dict[str, Any], check_id: str) -> str:
    security = evidence.get("security")
    if not isinstance(security, dict):
        return ""
    checks = security.get("checks")
    if not isinstance(checks, dict):
        return ""
    block = checks.get(check_id)
    if not isinstance(block, dict):
        return ""
    detail = block.get("details")
    return detail if isinstance(detail, str) else ""


def _parse(evidence: dict[str, Any], check_id: str, label: str) -> bool | None:
    """Read ``label=value`` out of one check's detail string."""
    detail = _details(evidence, check_id)
    for raw_key, raw_value in PAIR_RE.findall(detail):
        key = raw_key.strip().lower()
        # Details read "GoPlus honeypot=no" / "Mintable=no" — the label is a
        # SUFFIX of the raw key, so an exact compare would miss every prefixed
        # answer (that bug rendered a known honeypot answer as "—").
        if key == label or key.endswith(f" {label}"):
            return _as_bool(raw_value)
    return None


def normalize(r: dict[str, Any]) -> dict[str, Any]:
    """Return ``r`` with optional gaps filled from ``evidence.security``.

    Copies the dict (the scan cache is shared) and never overwrites a value the
    flat payload already carries. ``holders=0`` is treated as absent: the
    GoPlus check reports ``Holders=0`` for tokens with live supply, and a
    fabricated zero is the exact bug the card must not repeat.
    """
    out = dict(copy.deepcopy(r))
    evidence = out.get("evidence")
    if not isinstance(evidence, dict):
        return out
    for flat, (check_id, label) in CHECKS.items():
        if out.get(flat) is None:
            parsed = _parse(evidence, check_id, label)
            if parsed is not None:
                out[flat] = parsed
    holders = out.get("holders")
    if isinstance(holders, (int, float)) and not isinstance(holders, bool) and holders <= 0:
        out["holders"] = None
    return out
