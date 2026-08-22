"""AI "Analyst Read" — plain-language bundle/cluster interpretation.

Fail-open by design: empty intel, LLM timeout or blank answer all return ""
so the caller simply skips the block (never a blank section).
"""

from __future__ import annotations

import asyncio
import html
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)


_ANALYST_READ_TIMEOUT = 8.0


_ANALYST_READ_MAX_CHARS = 900


_ANALYST_READ_SYSTEM = (
    "You are the RugMunch deep-scan analyst. Write 2-3 SHORT plain-English "
    "sentences interpreting a token's bundle/cluster structure for a trader. "
    "Use ONLY the facts provided in the intel JSON — never invent numbers, "
    "addresses or claims. Explain what the structure suggests (e.g. coordinated "
    "launch vs organic demand) and why it matters. No markdown, no emojis, no "
    "headers, no apologies."
)


def _build_bundle_intel(data: dict[str, Any]) -> dict[str, Any]:
    """Compact bundle/cluster intel summary for the Analyst Read prompt.

    Pulls ONLY values the scan actually reported — absent values are omitted
    so the LLM never sees fabricated zeros. Reads the SENTINEL deepscan dict
    shape: ``bundle_detection`` (with ``same_slot``), ``wallet_map``,
    ``holder_analysis`` and ``contract_creation``.
    """
    bundle = _as_dict(data, "bundle_detection")
    same_slot = _as_dict(bundle, "same_slot")
    wallet_map = _as_dict(data, "wallet_map")
    holder = _as_dict(data, "holder_analysis")
    creation = _as_dict(data, "contract_creation")
    cluster = _as_dict(data, "cluster_analysis")

    intel: dict[str, Any] = {}

    # Bundle wallet count: prefer the own-engine bundle count, then the
    # SENTINEL deployer-funded + sniper lists, then the total bundled %.
    n_same_bundle = _safe_int(same_slot.get("bundle_wallets"))
    n_deployer = len(bundle.get("deployer_funded") or [])
    n_snipers = len(bundle.get("same_block_snipers") or [])
    n_bundle = n_same_bundle or (n_deployer + n_snipers) or None
    if n_bundle:
        intel["bundle_wallet_count"] = n_bundle
    # Same-slot concentration (Solana, Helius-backed).
    n_slot = _safe_int(same_slot.get("same_slot_wallets"))
    if n_slot:
        intel["same_slot_wallets"] = n_slot
    sol_spent = _safe_float(same_slot.get("same_slot_sol_spent"))
    if sol_spent:
        intel["same_slot_sol_spent"] = round(sol_spent, 2)
    held_pct = _safe_float(same_slot.get("same_slot_held_pct"))
    if held_pct:
        intel["same_slot_held_pct"] = round(held_pct, 1)
    # Wallet roles (dev/whale/fish/shrimp/fresh/bundle counts).
    roles = wallet_map.get("roles")
    if isinstance(roles, dict):
        present = {str(k): int(v) for k, v in roles.items() if _safe_int(v)}
        if present:
            intel["wallet_roles"] = present
    # Deployer funding source (SENTINEL contract-creation module).
    dfp = _safe_float(creation.get("deployer_funded_percentage"))
    if dfp is not None:
        intel["deployer_funded_pct"] = round(dfp, 1)
    # Holder concentration.
    top10 = _safe_float(holder.get("top_10_percentage"))
    hhi = _safe_float(holder.get("hhi"))
    if top10 is not None or hhi is not None:
        conc: dict[str, Any] = {}
        if top10 is not None:
            conc["top10_pct"] = round(top10, 1)
        if hhi is not None:
            conc["hhi"] = round(hhi, 0)
        intel["holder_concentration"] = conc

    # Buyer timing (own wallet_map engine): fresh-wallet % + recently-funded %
    # among the first-buyers. Absent values are omitted — never fabricated 0%.
    timing = _as_dict(wallet_map, "buyer_timing")
    fresh_pct = _safe_float(timing.get("fresh_wallet_pct"))
    funded_pct = _safe_float(timing.get("recently_funded_pct"))
    if fresh_pct is not None or funded_pct is not None:
        timing_intel: dict[str, Any] = {}
        if fresh_pct is not None:
            timing_intel["fresh_wallet_pct"] = round(fresh_pct, 0)
        if funded_pct is not None:
            timing_intel["recently_funded_pct"] = round(funded_pct, 0)
        fw_h = _safe_int(timing.get("fresh_window_hours"))
        ff_h = _safe_int(timing.get("funded_window_hours"))
        if fw_h:
            timing_intel["fresh_window_hours"] = fw_h
        if ff_h:
            timing_intel["funded_window_hours"] = ff_h
        intel["buyer_timing"] = timing_intel

    # Funding-source clusters (own cluster_analysis engine): funder→wallet
    # grouping + the coordinated / organic verdict. Only surfaced when the
    # engine actually scanned (result == "scanned").
    if str(cluster.get("result")) == "scanned":
        cluster_intel: dict[str, Any] = {}
        n_funders = _safe_int(cluster.get("distinct_funders"))
        n_wallets = _safe_int(cluster.get("funded_wallets"))
        largest = _safe_int(cluster.get("largest_cluster_size"))
        coord = _safe_int(cluster.get("coordinated_buyers"))
        chain_b = _safe_int(cluster.get("funding_chain_buyers"))
        verdict = str(cluster.get("verdict") or "")
        if n_funders:
            cluster_intel["distinct_funders"] = n_funders
        if n_wallets:
            cluster_intel["funded_wallets"] = n_wallets
        if largest:
            cluster_intel["largest_cluster_size"] = largest
        if coord:
            cluster_intel["coordinated_buyers"] = coord
        if chain_b:
            cluster_intel["funding_chain_buyers"] = chain_b
        if verdict:
            cluster_intel["cluster_verdict"] = verdict
        # Deeper dimensions (2-hop chain, supply grab, time nodes) — only
        # surfaced when the engine measured them.
        depth = _safe_int(cluster.get("funding_chain_depth"))
        if depth:
            cluster_intel["funding_chain_depth"] = depth
        two_hop = _safe_int(cluster.get("two_hop_chain_buyers"))
        if two_hop:
            cluster_intel["two_hop_chain_buyers"] = two_hop
        time_nodes = _safe_int(cluster.get("time_nodes"))
        if time_nodes:
            cluster_intel["time_nodes"] = time_nodes
        supply_grab = cluster.get("supply_grab")
        if isinstance(supply_grab, dict) and str(supply_grab.get("status") or "none") in ("holding", "dumped"):
            cluster_intel["supply_grab"] = {
                "status": str(supply_grab.get("status")),
                "held_pct": round(_safe_float(supply_grab.get("held_pct")) or 0.0, 1),
                "dumped_pct": round(_safe_float(supply_grab.get("dumped_pct")) or 0.0, 1),
            }
        if cluster_intel:
            intel["funding_clusters"] = cluster_intel

    return intel


def _as_dict(container: dict[str, Any], key: str) -> dict[str, Any]:
    """Return ``container[key]`` when it is a dict, else empty dict (safe)."""
    value = container.get(key)
    return value if isinstance(value, dict) else {}


def _safe_int(value: Any) -> int | None:
    """Best-effort int conversion (N/A-safe)."""
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    """Best-effort float conversion (N/A-safe)."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def render_analyst_read_block(data: dict[str, Any]) -> str:
    """Render the "🧠 Analyst Read" block over a deep-scan data dict.

    Builds the compact bundle/cluster intel summary, asks the snappy LLM
    (``app.llm_config.chat_with_fallback(messages, "snappy")`` — free
    OpenRouter tier with cheap paid fallback) for 2-3 plain-English sentences
    interpreting WHY the cluster structure matters, and returns an HTML-safe
    block. Fail-open: empty intel, timeout, rate-limit or blank answer all
    return "" so the caller skips the block entirely.
    """
    intel = _build_bundle_intel(data)
    if not intel:
        return ""
    import json

    from rugmunchbot.llm_config import chat_with_fallback

    messages: list[dict[str, str]] = [
        {"role": "system", "content": _ANALYST_READ_SYSTEM},
        {
            "role": "user",
            "content": (
                "Token bundle/cluster intel (JSON, only facts we measured):\n"
                f"{json.dumps(intel, default=str)[:800]}\n\n"
                "Write the Analyst Read now."
            ),
        },
    ]
    try:
        text = await asyncio.wait_for(chat_with_fallback(messages, "snappy"), timeout=_ANALYST_READ_TIMEOUT)
    except Exception as e:
        logger.warning("analyst read generation failed: %s", e)
        text = ""
    text = (text or "").strip()
    if not text:
        return ""
    return f"\U0001f9e0 <b>Analyst Read</b>\n{html.escape(text)[:_ANALYST_READ_MAX_CHARS]}"
