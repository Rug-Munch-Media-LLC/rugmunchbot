"""Canonical scanner engine for RugMunchBot — package layout.

Split from the monolithic ``tools/scanner.py`` (2026-08-16) into focused
submodules by responsibility. This ``__init__`` is a pure re-export shim:
every public name and signature is unchanged, and callers/tests keep
importing ``app.domains.telegram.rugmunchbot.tools.scanner`` exactly as
before. Patchable names (``httpx``, ``backend_json_post``,
``schedule_scan_memory_index``, ``token_meta``) stay bound here so tests
that monkeypatch ``tools.scanner.*`` keep working.

Layout:
  * ``_cache.py``     — short-TTL in-process scan cache
  * ``_providers.py`` — EVM/RMI security providers + coercion helpers
  * ``_market.py``    — DexScreener best-pair market snapshot helpers
  * ``_deployer.py``  — deployer address + socials resolution
  * ``_security.py``  — evidence security checks + honeypot verdict
  * ``_flatten.py``   — payload shape converters + v2->legacy
  * ``_ai_verdict.py``— snappy-tier AI verdict
  * ``_scans.py``     — token/wallet scan engines + databus fetchers
  * ``_intel.py``     — chat scanner tool + self-registration
"""

from __future__ import annotations

import httpx as httpx

from app.domains.scanner.gmgn_enrich import enrich_scan_with_gmgn as enrich_scan_with_gmgn
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client as get_shared_client
from app.domains.telegram.rugmunchbot.tools.registry import (
    ToolResult as ToolResult,
    backend_json_post as backend_json_post,
)
from app.domains.telegram.rugmunchbot.tools.scanner._ai_verdict import (
    _AI_VERDICT_FACTS_CHARS as _AI_VERDICT_FACTS_CHARS,
    _AI_VERDICT_MAX_CHARS as _AI_VERDICT_MAX_CHARS,
    _AI_VERDICT_SYSTEM as _AI_VERDICT_SYSTEM,
    _AI_VERDICT_TIMEOUT as _AI_VERDICT_TIMEOUT,
    _llm_verdict_text as _llm_verdict_text,
    _verdict_facts as _verdict_facts,
    render_ai_verdict as render_ai_verdict,
)
from app.domains.telegram.rugmunchbot.tools.scanner._cache import (
    _SCAN_CACHE_TTL as _SCAN_CACHE_TTL,
    _scan_cache as _scan_cache,
    _scan_cache_get as _scan_cache_get,
    _scan_cache_key as _scan_cache_key,
    _scan_cache_set as _scan_cache_set,
)
from app.domains.telegram.rugmunchbot.tools.scanner._deployer import (
    _EMPTY_ADDR as _EMPTY_ADDR,
    _birdeye_creator as _birdeye_creator,
    _extract_pair_intel as _extract_pair_intel,
    _supplement_deployer_socials as _supplement_deployer_socials,
    _token_meta_dict as _token_meta_dict,
)
from app.domains.telegram.rugmunchbot.tools.scanner._flatten import (
    _flatten_dexscreener as _flatten_dexscreener,
    _flatten_v1 as _flatten_v1,
    _flatten_v2 as _flatten_v2,
    v2_token_to_legacy as v2_token_to_legacy,
    v2_wallet_to_legacy as v2_wallet_to_legacy,
)
from app.domains.telegram.rugmunchbot.tools.scanner._intel import (
    register_extra_tools as register_extra_tools,
    scanner_intel as scanner_intel,
)
from app.domains.telegram.rugmunchbot.tools.scanner._market import (
    _DS_PRICE_CHANGE_TFS as _DS_PRICE_CHANGE_TFS,
    _DS_VOLUME_TFS as _DS_VOLUME_TFS,
    _ds_market_snapshot as _ds_market_snapshot,
    _ds_timeframe_map as _ds_timeframe_map,
    _merge_market_snapshot as _merge_market_snapshot,
)
from app.domains.telegram.rugmunchbot.tools.scanner._providers import (
    _detect_chain as _detect_chain,
    _safe_float as _safe_float,
    _scan_evm_security_provider as _scan_evm_security_provider,
    _scan_rmi_provider as _scan_rmi_provider,
)
from app.domains.telegram.rugmunchbot.tools.scanner._scans import (
    analyze_wallet as analyze_wallet,
    analyze_wallet_v2 as analyze_wallet_v2,
    enrich_scan as enrich_scan,
    fetch_deployer_fingerprint as fetch_deployer_fingerprint,
    fetch_hype_score as fetch_hype_score,
    scan_token as scan_token,
    scan_token_v2 as scan_token_v2,
    sentinel_scan as sentinel_scan,
)
from app.domains.telegram.rugmunchbot.tools.scanner._security import (
    _honeypot_verdict as _honeypot_verdict,
    _security_checks as _security_checks,
)
from app.domains.telegram.rugmunchbot.tools.token_meta import token_meta as token_meta
from app.rag.scan_memory import (
    schedule_scan_memory_index as schedule_scan_memory_index,
    should_index_scan as should_index_scan,
)

# Self-registration: importing this package wires the extra tools into the
# shared registry singleton (see tools/__init__.py additive import).
register_extra_tools()
