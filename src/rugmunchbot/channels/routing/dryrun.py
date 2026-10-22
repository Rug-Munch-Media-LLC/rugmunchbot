"""Dry-run proof for the channel routing table.

Builds one representative payload per channel, runs it through
:func:`rugmunchbot.channels.routing.gate.decide`, and prints the gate decision
(sent / suppressed + reason). Nothing is sent to Telegram — this module never
imports the transport, so a dry-run proof can never leak a live post.

Run it:

    python -m rugmunchbot.channels.routing.dryrun

Exit code 0 when every expected decision matches (positive sends pass, negative
cases are suppressed for the documented reason), 1 otherwise.
"""

from __future__ import annotations

import json
from typing import Any

from rugmunchbot.channels.routing.gate import decide, reset_buckets

#: (channel, category, severity, payload, expected_allowed, expected_reason)
CASES: tuple[tuple[str, str, str, dict[str, Any], bool, str], ...] = (
    (
        "rmialerts",
        "exploit",
        "critical",
        {
            "title": "Live exploit draining approvals on 0x9f...c1",
            "body": "Attacker contract 0x9f...c1 is sweeping USDC approvals. Revoke now.",
            "token_address": "0x9f3a...c1",
            "chain": "ethereum",
            "urls": ["https://revoke.cash"],
            "source": "rugbrain",
        },
        True,
        "ok",
    ),
    (
        "rmialpha",
        "whale",
        "high",
        {
            "title": "Whale moved 12.4M USDC into SOL",
            "body": "Wallet 7xKq... (funded by Jump) swapped 12.4M USDC -> 71,300 SOL.",
            "token_address": "So111...112",
            "chain": "solana",
            "source": "whale_watch",
        },
        True,
        "ok",
    ),
    (
        "rmiscans",
        "launch",
        "medium",
        {
            "title": "New launch: RUGLESS on Solana",
            "body": "Fresh pair, $41k liquidity, 12m old, deployer has 3 prior rugs.",
            "token_address": "RUGL...ess",
            "chain": "solana",
            "source": "launch_watch",
        },
        True,
        "ok",
    ),
    (
        "rmicryptonews",
        "news",
        "low",
        {
            "title": "SEC drops appeal in token-classification case",
            "summary": "The regulator withdrew its appeal, ending an 18-month fight.",
            "link": "https://example.com/sec-appeal",
            "source": "theblock",
        },
        True,
        "ok",
    ),
    # ── negative cases: the audit findings this table exists to stop ──────
    (
        "rmialpha",
        "launch",
        "high",
        {"title": "New Launches (hourly list)", "source": "launch_watch"},
        False,
        "category_redirected",
    ),
    (
        "rmialerts",
        "scanner",
        "medium",
        {"title": "Risk batch — 14 tokens scanned", "source": "scanner"},
        False,
        "category_not_allowed",
    ),
    (
        "rmialerts",
        "rug_pull",
        "medium",
        {"title": "Medium-severity rug signal", "source": "scanner"},
        False,
        "severity_below_floor",
    ),
    (
        "munchscans",
        "alert",
        "critical",
        {"title": "duplicate channel test"},
        False,
        "archived",
    ),
    (
        "crmunchers",
        "promo",
        "critical",
        {"title": "promo spam"},
        False,
        "manual_only",
    ),
)

#: Extra alerts used to exhaust @rmialerts' 5/hour budget before the 6th.
_FILLER = (
    ("honeypot", "critical", {"title": "Honeypot cluster #2"}),
    ("rug_pull", "high", {"title": "Rug confirmed #3"}),
    ("mint", "high", {"title": "Hidden mint #4"}),
    ("blacklist", "high", {"title": "Blacklist function #5"}),
)
#: The sixth alert in the same hour — must be rate limited.
_OVERFLOW = ("deployer_sell", "critical", {"title": "Deployer sell #6 (same hour)"})


def _row(channel: str, category: str, severity: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Decide one item and shape it as a report row."""
    decision = decide(channel, category, severity)
    return {
        "channel": channel,
        "category": category,
        "severity": severity,
        "decision": "sent" if decision.allowed else "suppressed",
        "live": decision.live,
        "reason": decision.reason,
        "redirect": decision.redirect,
        "payload": payload,
    }


def _check(row: dict[str, Any], expected: bool, reason: str) -> bool:
    """True when the row's gate decision matches the expectation."""
    ok = row["decision"] == ("sent" if expected else "suppressed") and row["reason"] == reason
    if not ok:
        row["unexpected"] = f"expected sent={expected} reason={reason}"
    return ok


def main() -> int:
    """Print every decision as one JSON line; return a CI-friendly exit code."""
    reset_buckets()
    unexpected = 0
    rows: list[dict[str, Any]] = []

    for channel, category, severity, payload, expected, reason in CASES:
        row = _row(channel, category, severity, payload)
        unexpected += 0 if _check(row, expected, reason) else 1
        rows.append(row)
        if channel == "rmialerts" and category == "exploit":
            # Alert #1 consumed one slot; burn the remaining four.
            for fill_cat, fill_sev, fill_payload in _FILLER:
                filler = _row(channel, fill_cat, fill_sev, fill_payload)
                if expected is True:
                    unexpected += 0 if _check(filler, True, "ok") else 1
                rows.append(filler)
            overflow = _row(channel, *_OVERFLOW)
            unexpected += 0 if _check(overflow, False, "rate_limited") else 1
            rows.append(overflow)

    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    print(json.dumps({"cases": len(rows), "unexpected": unexpected}))
    return 0 if unexpected == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
