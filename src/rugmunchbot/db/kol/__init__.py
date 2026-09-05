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
scoring logic lives in ``channels.kol_scoreboard`` — this package only
stores and fetches rows.

Sub-modules:
* ``calls``     — kol_calls rows (record / mark deleted / list / export)
* ``scores``    — cached per-handle report-card aggregates
* ``identity``  — canonical identities, aliases, name changes, groups
* ``wallets``   — wallet knowledgebase, clustering, wallet-KB seeding
* ``history``   — documented rug/dump history seeding
* ``usage``     — ApiPass credit meter
"""

from __future__ import annotations

from .calls import (
    export_kol_calls_jsonl,
    get_kol_call,
    list_kol_calls,
    mark_kol_call_deleted,
    record_kol_call,
    update_kol_call_result,
)
from .history import seed_kol_rug_history
from .identity import (
    demote_alias,
    get_kol_identity,
    kol_identities_by_group,
    kol_registry_count,
    list_kol_aliases,
    list_kol_groups,
    list_kol_identities,
    list_kol_name_changes,
    record_kol_name_change,
    register_alias,
    resolve_handle,
    seed_kol_registry,
    seed_scammer_groups,
    upsert_kol_identity,
)
from .scores import get_kol_scores, upsert_kol_score
from .usage import apipass_usage_summary, record_apipass_usage
from .wallets import (
    attach_kol_wallet,
    cluster_kols_by_wallet,
    kol_identities_by_wallet,
    link_identity_via_wallet,
    list_kol_wallets,
    mark_kol_verified,
    seed_wallet_kb,
    wallet_clusters,
    wallet_kb_by_source,
)

__all__ = [
    "apipass_usage_summary",
    "attach_kol_wallet",
    "cluster_kols_by_wallet",
    "demote_alias",
    "export_kol_calls_jsonl",
    "get_kol_call",
    "get_kol_identity",
    "get_kol_scores",
    "kol_identities_by_group",
    "kol_identities_by_wallet",
    "kol_registry_count",
    "link_identity_via_wallet",
    "list_kol_aliases",
    "list_kol_calls",
    "list_kol_groups",
    "list_kol_identities",
    "list_kol_name_changes",
    "list_kol_wallets",
    "mark_kol_call_deleted",
    "mark_kol_verified",
    "record_apipass_usage",
    "record_kol_call",
    "record_kol_name_change",
    "register_alias",
    "resolve_handle",
    "seed_kol_registry",
    "seed_kol_rug_history",
    "seed_scammer_groups",
    "seed_wallet_kb",
    "update_kol_call_result",
    "upsert_kol_identity",
    "upsert_kol_score",
    "wallet_clusters",
    "wallet_kb_by_source",
]
