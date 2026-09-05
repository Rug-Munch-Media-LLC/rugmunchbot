"""KOL Scoreboard — per-KOL report cards, name-change tracking, wallet KB.

The renamed main product (2026-08-29): what was "KOL Hall of Shame" is now
the **KOL Scoreboard** — the full per-KOL report-card product over every
tracked crypto KOL / shiller. "Hall of Shame" survives as the *subset* of
**repeat scammers** (≥2 deleted calls, ≥2 known-scam-token calls, or ≥3
shill calls) — deliberately NOT every KOL with a low grade.

v9 (2026-08-29): **scammer groups** (syndicate attribution) + **wallet
tracing expansion**. Every identity can carry a ``scammer_group`` slug
(``solana_kol_syndicate``, ``impersonation_syndicate``,
``celebrity_tout_syndicate``, … — labels resolve via
``kol_registry_seed.group_label``). The Hall of Shame and scoreboard posts
render the group. Wallet tracing now exposes per-wallet chain + confidence
(``list_kol_wallets``), inference linking from call tweets
(``link_wallet_from_call``), provenance-source lookup
(``wallet_kb_by_source``, e.g. ``zachxbt_post`` / ``dethective`` when the
operator seeds evidence-linked entries), and on-chain clustering of
identities that share a wallet (``wallet_clusters`` /
``cluster_kols_by_wallet``).

How it works (bounty-hunter model)
----------------------------------
1. ``run_kol_scoreboard_watch`` sweeps the seeded KOL registry
   (``kol_registry_seed`` — ~565 real accounts from the fleet's curated
   registries) via the ApiPass X source, one handle per tick
   (round-robin, ≤5 task-creations/hr ≈ 3 credits/hr).
2. Every call-looking tweet is recorded via ``db.kol.record_kol_call``
   (upsert by tweet_id).
3. ``detect_name_change`` — when a swept handle's payload carries a
   *different* screen_name, the transition is recorded
   (``db.kol.record_kol_name_change``), the old alias demoted and the new
   one registered — so a renamed KOL keeps ONE report card (100% correct
   identification across screen-name changes, e.g. altcoingordon).
   ``db.kol.list_kol_aliases`` returns the full historical alias list.
4. ``detect_deleted_calls`` — a recorded tweet that now 404s on ApiPass is
   marked deleted (retraction = the strongest dishonesty signal).
5. Wallet tracing — wallet-shaped addresses in call texts are attributed to
   the KOL's wallet KB via inference (``link_wallet_from_call``, chain from
   address shape, confidence ``inference``), the 945-wallet knowledgebase
   is seeded from fleet data with chain/confidence/provenance
   (``attach_kol_wallet`` / ``seed_wallet_kb``), and shared wallets across
   identities produce on-chain clusters (``wallet_clusters``) plus identity
   links (``link_identity_via_wallet`` / ``cluster_kols_by_wallet``).
6. ``compute_report_card`` (pure) aggregates: calls, deletions, shills,
   scam-token calls, win/loss (from documented outcomes), name changes,
   scammer group, traced wallets, score + grade.
7. The SCOREBOARD post (worst-first, deduped per day) goes to @rmialerts;
   the HALL OF SHAME subset is only repeat scammers — and shows the group.

Pure + deterministic where possible; fail-open at every stage.

Package layout (split from the former 1004-line monolith):
- ``data``       — data model, config, sweep/identity/export (I/O)
- ``scoring``    — pure classification, aggregation, scoring, ranking
- ``formatting`` — Telegram HTML rendering
- ``publish``    — the scheduler runner that posts to @rmialerts
"""

from __future__ import annotations

from rugmunchbot.channels.kol_scoreboard.data import (
    FRESH_CALL_HOURS,
    KOL_SCOREBOARD_ENABLED,
    KOL_SCOREBOARD_INTERVAL_MIN,
    KOL_SCOREBOARD_MAX_ALERTS,
    KOL_TRACKED_HANDLES,
    KolCall,
    KolIdentity,
    KolReportCard,
    build_scoreboard_cards,
    detect_deleted_calls,
    detect_name_change,
    link_wallet_from_call,
    resolve_identity,
    sweep_tracked_kol_tweets,
)
from rugmunchbot.channels.kol_scoreboard.formatting import (
    format_hall_of_shame_post,
    format_report_card,
    format_scoreboard_post,
)
from rugmunchbot.channels.kol_scoreboard.publish import run_kol_scoreboard_watch
from rugmunchbot.channels.kol_scoreboard.scoring import (
    classify_call,
    compute_report_card,
    extract_tokens,
    extract_wallet_address,
    grade_for,
    rank_hall_of_shame,
    rank_scoreboard,
    score_card,
)

__all__ = [
    "FRESH_CALL_HOURS",
    "KOL_SCOREBOARD_ENABLED",
    "KOL_SCOREBOARD_INTERVAL_MIN",
    "KOL_SCOREBOARD_MAX_ALERTS",
    "KOL_TRACKED_HANDLES",
    "KolCall",
    "KolIdentity",
    "KolReportCard",
    "build_scoreboard_cards",
    "classify_call",
    "compute_report_card",
    "detect_deleted_calls",
    "detect_name_change",
    "extract_tokens",
    "extract_wallet_address",
    "format_hall_of_shame_post",
    "format_report_card",
    "format_scoreboard_post",
    "grade_for",
    "link_wallet_from_call",
    "rank_hall_of_shame",
    "rank_scoreboard",
    "resolve_identity",
    "run_kol_scoreboard_watch",
    "score_card",
    "sweep_tracked_kol_tweets",
]
