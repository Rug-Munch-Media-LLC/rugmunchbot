"""Pure helper functions for SENTINEL / scan-report formatting (Batch 13).

Extracted from ``formatters/_sentinel.py``: blue-chip allowlist, sentinel
module emoji + risk emoji maps, warning decoding, recommendation builder,
USD amount formatting, and the compact intel lines (bundle / deployer /
wallet freshness). No bot or keyboard dependencies — only ``config``
formatting primitives and standard library.
"""

from __future__ import annotations

import html
from typing import Any

# --- SENTINEL deep forensic flags (v2 /scan + deep scan) ---

# Known blue-chip allowlist — imported lazily from the unified scanner
# scoring module (app/domains/scanner/unified/scoring.py) so the bot never
# hardcodes token addresses. Blue-chips (WETH, WBTC, USDC, USDT, DAI, stETH)
# legitimately carry high HHI / low LP-lock signals; those warnings are
# suppressed in /scan output (Bug 1 in scoring.py).
_BLUECHIP_CACHE: frozenset[str] | None = None


def _bluechip_addresses() -> frozenset[str]:
    global _BLUECHIP_CACHE
    if _BLUECHIP_CACHE is None:
        try:
            from app.domains.scanner.unified.scoring import BLUECHIP_ADDRESSES

            _BLUECHIP_CACHE = frozenset(a.lower() for a in BLUECHIP_ADDRESSES)
        except ImportError:  # pragma: no cover - defensive
            _BLUECHIP_CACHE = frozenset()
    return _BLUECHIP_CACHE


def is_bluechip(address: str) -> bool:
    """True for known blue-chip tokens (case-insensitive allowlist)."""
    return (address or "").strip().lower() in _bluechip_addresses()


def _sentinel_free(sentinel: dict[str, Any] | None) -> dict[str, Any]:
    """Extract the free-module bucket from a v2 sentinel dict (best-effort)."""
    if not isinstance(sentinel, dict):
        return {}
    free = sentinel.get("free")
    return free if isinstance(free, dict) else {}


def _module_warnings(free: dict[str, Any], *keys: str) -> list[str]:
    """Collect warnings across the given sentinel free modules (deduped)."""
    seen: list[str] = []
    for key in keys:
        mod = free.get(key)
        if isinstance(mod, dict):
            for w in mod.get("warnings") or []:
                w_str = str(w).strip()
                if w_str and w_str not in seen:
                    seen.append(w_str)
    return seen


# --- SENTINEL deep scan report (from POST /api/v1/x402-tools/sentinel_scan) ---

SENTINEL_MODULE_EMOJI: dict[str, str] = {
    "holder_analysis": "\U0001f465",  # 👥
    "liquidity_verification": "\U0001f510",  # 🔐
    "dev_reputation": "\U0001f468\u200d\U0001f4bb",  # 👨‍💻
    "metadata_fingerprint": "\U0001f5c2\ufe0f",  # 🗒️
    "contract_authority": "\U0001f512",  # 🔒
    "proxy_detection": "\U0001f4e6",  # 📦
    "bundle_detection": "\U0001f4e6",  # 📦
    "exchange_funding": "\U0001f3e6",  # 🏦
    "wash_trading": "\U0001f504",  # 🔄
    "honeypot_detection": "\U0001f3ef",  # 🏯
    "sentiment": "\U0001f4ac",  # 💬
    "pumpfun_analysis": "\U0001f680",  # 🚀
    "mev_detection": "\U0001f50d",  # 🔍
    "flash_loan_detection": "\u26a1",  # ⚡
    "pump_dump_detection": "\U0001f4c8",  # 📈
    "oracle_manipulation": "\U0001f4b0",  # 💰
    "governance_attack": "\U0001f3db\ufe0f",  # 🏛️
    "static_analysis": "\U0001f50e",  # 🔎
    "address_labels": "\U0001f3f7\ufe0f",  # 🏷️
    "block_zero_sniper": "\U0001f3af",  # 🎯
    "gas_trace": "\u26a1",  # ⚡
    "cross_chain_trace": "\U0001f517",  # 🔗
}


def _risk_level_emoji(level: str) -> str:
    lvl = str(level or "").lower()
    if lvl in ("critical", "dangerous"):
        return "\U0001f534"  # 🔴
    if lvl in ("high", "suspicious"):
        return "\U0001f7e1"  # 🟠
    if lvl in ("medium", "moderate"):
        return "\U0001f7e0"  # 🟡
    return "\U0001f7e2"  # 🟢


def _sentinel_recommendations(risk: float, level: str, red_flags: list[Any]) -> list[str]:
    """Build DYOR recommendations from sentinel risk signals."""
    lvl = str(level or "").lower()
    recs: list[str] = []
    if risk >= 70 or lvl in ("critical", "high"):
        recs.append("\U0001f534 Avoid this token \u2014 critical risk signals detected")
    elif risk >= 40:
        recs.append("\U0001f7e1 Proceed with extreme caution \u2014 elevated risk")
    else:
        recs.append("\U0001f7e2 Low risk \u2014 standard DeFi caution still applies")
    joined = " ".join(str(f).lower() for f in red_flags)
    if "honeypot" in joined:
        recs.append("\U0001f3ef Honeypot signal \u2014 verify you can actually SELL before buying")
    if "bundle" in joined:
        recs.append("\U0001f4e6 Bundle activity \u2014 volume & concentration may be fabricated")
    if "wash" in joined:
        recs.append("\U0001f504 Wash trading suspected \u2014 volume may be fake")
    recs.append("\U0001f50d Always verify with a second source: /scan <code>address</code>")
    return recs[:6]


# --- Scan-report intel helpers (own-data engines) ---


def _bundle_alert_line(r: dict[str, Any]) -> str | None:
    """Red alert banner when the own bundle-launch engine flags a bundled launch.

    Reads ``r["bundle_analysis"]`` (``is_bundled`` / ``confidence`` /
    ``risk_label``) and returns a single prominent banner line, or ``None``
    when the scanner found no coordinated launch (or no bundle data).
    """
    ba = r.get("bundle_analysis")
    if not isinstance(ba, dict) or not ba.get("is_bundled"):
        return None
    try:
        conf = float(ba.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    risk = html.escape(str(ba.get("risk_label") or "unknown")).upper()
    return f"\U0001f6a8 <b>BUNDLED LAUNCH DETECTED</b> \u2014 {conf:.0f}% confidence, {risk}"


def _deployer_history_line(r: dict[str, Any]) -> str | None:
    """Repeat-deployer track record line, or ``None`` when no profile exists.

    Reads ``r["deployer_profile"]`` (own ``deployer_profiles`` engine) which
    carries ``total_tokens_launched`` / ``total_deployments``, ``rugged_pct``
    (0-1 fraction), and ``avg_lifespan_days``. Falls back to the SENTINEL
    deep-profile ``rug_rate`` (already a percentage) when only that is set.
    """
    dp = r.get("deployer_profile")
    if not isinstance(dp, dict) or not dp:
        return None
    tokens = int(dp.get("total_tokens_launched") or dp.get("total_deployments") or 0)
    rugged_raw = dp.get("rugged_pct")
    if rugged_raw is None:
        rugged_raw = dp.get("rug_rate", 0.0)
    try:
        rugged_f = float(rugged_raw or 0.0)
    except (TypeError, ValueError):
        rugged_f = 0.0
    rugged_pct = round(rugged_f * 100) if rugged_f <= 1.0 else round(rugged_f)
    life_days = dp.get("avg_lifespan_days")
    if life_days is None:
        life_str = "unknown"
    else:
        try:
            life_f = float(life_days)
        except (TypeError, ValueError):
            life_f = 0.0
        life_str = f"{round(life_f)} days" if life_f >= 1 else f"{round(life_f * 24)} hours"
    return f"\U0001f464 <b>Repeat Deployer</b>: {tokens} tokens, {rugged_pct}% rugged, avg life {life_str}"


def _wallet_freshness_line(r: dict[str, Any]) -> str | None:
    """Wallet freshness + CEX-funding + brand-new line, or ``None`` when no metrics.

    Reads ``r["wallet_metrics"]`` (``fresh_wallet_pct`` / ``cex_funded_pct`` /
    ``first_tx_pct``) and returns a compact line. Skips the graceful-degradation
    payload (``result == "unavailable"``) so a failed engine never renders zeros.
    """
    wm = r.get("wallet_metrics")
    if not isinstance(wm, dict) or wm.get("result") == "unavailable":
        return None
    try:
        fresh = float(wm.get("fresh_wallet_pct") or 0)
        cex = float(wm.get("cex_funded_pct") or 0)
        first_tx = float(wm.get("first_tx_pct") or 0)
    except (TypeError, ValueError):
        return None
    n = int(wm.get("holders_analyzed") or 0)
    scope = f"top {n} holders" if n else "holders"
    return (
        f"\U0001f45b {scope.capitalize()}: \U0001f195 {fresh:.0f}% fresh (<30d) · "
        f"\U0001f3e6 {cex:.0f}% CEX-funded · \u2728 {first_tx:.0f}% brand-new"
    )


# ── SENTINEL warning code → human-readable description map ──
_SENTINEL_CODE_MAP = {
    "CR01": "Critical: Honeypot detected — sells blocked",
    "CR02": "Critical: Trading cooldown active",
    "CR03": "Critical: Hidden mint function",
    "CR04": "Critical: Owner can blacklist addresses",
    "CR05": "Critical: Transfer restrictions",
    "CR06": "Critical: Extreme fee manipulation",
    "CR07": "Critical: Anti-whale can be disabled by owner",
    "HI01": "High: Extreme holder concentration",
    "HI02": "High: Very low liquidity",
    "HI03": "High: Ownership not renounced",
    "HI04": "High: Upgradeable proxy contract",
    "HI05": "High: Dev wallet holds significant supply",
    "MD01": "Medium: LP lock below 6 months",
    "MD02": "Medium: Contract not verified",
    "MD03": "Medium: High buy/sell taxes",
    "MD04": "Medium: Low holder count",
    "MD05": "Medium: Recent contract creation",
    "RP01": "Risk: Dev has prior rug history",
    "RP02": "Risk: Dev reputation flagged",
    "RP03": "Risk: Similar to known scam bytecode",
    "RP04": "Risk: Funding from mixer/tumbler",
    "RP05": "Risk: Deployer-funded wallets detected",
    "RP06": "Risk: Wash trading patterns",
    "RP07": "Risk: Bundle launch detected",
    "RP08": "Risk: Abnormal volume/mcap ratio",
}


def _decode_sentinel_warning(w: str) -> str:
    """Convert a raw sentinel warning (possibly containing codes like CR01) to a human-readable string."""
    w_str = str(w).strip()
    # Try to map known codes at the start of the warning
    for code, desc in _SENTINEL_CODE_MAP.items():
        if f"[{code}]" in w_str or w_str.startswith((code, f"{code}:")):
            # If there's additional text after the code, append it
            remainder = w_str.replace(code, "").replace(f"[{code}]", "").replace(":", "").strip()
            if remainder and not any(remainder.startswith(c) for c in _SENTINEL_CODE_MAP):
                return f"{desc} — {remainder}"
            return desc
    # No code found — return cleaned-up original
    return w_str.replace("_", " ").replace("  ", " ").strip()


def _fmt_usd_amount(v) -> str:
    """Format a USD amount with proper suffixes (K, M, B)."""
    if v is None:
        return "N/A"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "N/A"
    if v == 0:
        return "$0"
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    if v >= 1:
        return f"${v:,.2f}"
    return f"${v:.6f}"
