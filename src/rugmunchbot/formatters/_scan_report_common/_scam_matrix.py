"""Consolidated Scam Signals risk matrix (16 techniques, honest "—")."""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.formatters._scan_report_common._fields import (
    _bundle_wallet_count,
    _cex_funded_pct,
    _fake_volume_pct,
    _freeze_auth,
    _lp_lock_pct,
    _mint_auth,
    _token_2022_flag,
)
from rugmunchbot.formatters._scan_report_common._layout import _THIN_SEP

_SCAM_SIGNAL_RED = "\U0001f534"


_SCAM_SIGNAL_GREEN = "\u2705"


_SCAM_SIGNAL_UNKNOWN = "\u2014"


def _evidence_security(r: dict[str, Any]) -> dict[str, Any]:
    """Best-effort GoPlus/own security evidence dict from any report shape."""
    ev = r.get("evidence")
    if isinstance(ev, dict):
        sec = ev.get("security")
        if isinstance(sec, dict):
            return sec
    sec = r.get("security")
    return sec if isinstance(sec, dict) else {}


def _risk_flag_set(r: dict[str, Any]) -> set[str]:
    """Normalized set of risk flags / threats from any report shape."""
    flags: set[str] = set()
    for key in ("threats", "risk_flags"):
        raw = r.get(key)
        if isinstance(raw, list):
            flags.update(str(f).lower() for f in raw if f)
    rs = r.get("risk_signals")
    if isinstance(rs, dict):
        score = rs.get("rug_risk_score")
        if isinstance(score, dict):
            sf = score.get("flags")
            if isinstance(sf, list):
                flags.update(str(f).lower() for f in sf if f)
    return flags


def _honeypot_flat(r: dict[str, Any]) -> bool | None:
    """Honeypot flag from flat report keys (bool or 'detected'/'none')."""
    raw = r.get("honeypot")
    if raw is None:
        sec = _evidence_security(r)
        raw = sec.get("is_honeypot")
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if s in ("detected", "yes", "true", "1"):
        return True
    if s in ("none", "no", "false", "0", "clean"):
        return False
    return None


def _scam_signal_rows(r: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Build the (label, state, detail) rows for the Scam Signals matrix.

    state is one of ``_SCAM_SIGNAL_RED`` / ``_SCAM_SIGNAL_GREEN`` /
    ``_SCAM_SIGNAL_UNKNOWN``. Detail is HTML-escaped by the caller. Every
    technique is listed — a missing data source renders as "—", never as a
    fabricated "clean".
    """
    sec = _evidence_security(r)
    flags = _risk_flag_set(r)
    rows: list[tuple[str, str, str]] = []

    # 1. Honeypot (sell-block)
    hp = _honeypot_flat(r)
    if hp is True or "honeypot" in flags:
        rows.append(("Honeypot (sell-block)", _SCAM_SIGNAL_RED, "sells may be blocked"))
    elif hp is False:
        rows.append(("Honeypot (sell-block)", _SCAM_SIGNAL_GREEN, "sellable"))
    else:
        rows.append(("Honeypot (sell-block)", _SCAM_SIGNAL_UNKNOWN, ""))

    # 2. Rug / LP removal risk
    selfdestruct = sec.get("selfdestruct")
    lp = _lp_lock_pct(r)
    if selfdestruct is True or (lp is not None and lp < 50):
        detail = "contract selfdestruct" if selfdestruct is True else f"LP only {lp:.0f}% locked"
        rows.append(("Rug / LP removal", _SCAM_SIGNAL_RED, detail))
    elif lp is not None:
        rows.append(("Rug / LP removal", _SCAM_SIGNAL_GREEN, f"LP {lp:.0f}% locked"))
    elif selfdestruct is False:
        rows.append(("Rug / LP removal", _SCAM_SIGNAL_GREEN, "no selfdestruct"))
    else:
        rows.append(("Rug / LP removal", _SCAM_SIGNAL_UNKNOWN, ""))

    # 3. Bundle / sniper launch
    ba = r.get("bundle_analysis")
    bundled = ba.get("is_bundled") if isinstance(ba, dict) else None
    if bundled is True or r.get("bundle_detected") or "bundled_launch" in flags:
        n = _bundle_wallet_count(ba) if isinstance(ba, dict) else None
        detail = f"{n} wallets" if n is not None else "coordinated launch"
        rows.append(("Bundle / sniper", _SCAM_SIGNAL_RED, detail))
    elif isinstance(ba, dict) and bundled is False:
        rows.append(("Bundle / sniper", _SCAM_SIGNAL_GREEN, "no bundle"))
    else:
        rows.append(("Bundle / sniper", _SCAM_SIGNAL_UNKNOWN, ""))

    # 4. Same-slot bundler (Solana)
    same_wallets = ba.get("same_slot_wallets") if isinstance(ba, dict) else None
    if isinstance(same_wallets, (int, float)) and same_wallets >= 3:
        rows.append(
            ("Same-slot bundler", _SCAM_SIGNAL_RED, f"{int(same_wallets)} wallets same slot")
        )
    elif isinstance(same_wallets, (int, float)):
        rows.append(("Same-slot bundler", _SCAM_SIGNAL_GREEN, f"{int(same_wallets)} same-slot"))
    else:
        rows.append(("Same-slot bundler", _SCAM_SIGNAL_UNKNOWN, ""))

    # 5. Funding-chain (CEX-funded / coordinated funding)
    cex = _cex_funded_pct(r)
    if cex is not None:
        if cex > 60:
            rows.append(("Funding-chain", _SCAM_SIGNAL_RED, f"{cex:.0f}% CEX-funded"))
        else:
            rows.append(("Funding-chain", _SCAM_SIGNAL_GREEN, f"{cex:.0f}% CEX-funded"))
    else:
        rows.append(("Funding-chain", _SCAM_SIGNAL_UNKNOWN, ""))

    # 6. Fake volume (wash trading)
    fake_vol = _fake_volume_pct(r)
    if fake_vol is not None:
        if fake_vol > 10:
            rows.append(("Fake volume (wash)", _SCAM_SIGNAL_RED, f"~{fake_vol:.0f}% fabricated"))
        else:
            rows.append(("Fake volume (wash)", _SCAM_SIGNAL_GREEN, f"~{fake_vol:.0f}% fabricated"))
    elif "wash_trading" in flags:
        rows.append(("Fake volume (wash)", _SCAM_SIGNAL_RED, "flagged"))
    else:
        rows.append(("Fake volume (wash)", _SCAM_SIGNAL_UNKNOWN, ""))

    # 7. Deployer dump (team wallet)
    dump = sec.get("deployer_dump_detected")
    if dump is True:
        count = sec.get("deployer_dump_count")
        detail = (
            f"{count} deployer sell txns"
            if isinstance(count, (int, float)) and count
            else "deployer selling"
        )
        rows.append(("Deployer dump", _SCAM_SIGNAL_RED, detail))
    elif dump is False:
        rows.append(("Deployer dump", _SCAM_SIGNAL_GREEN, "no team sell detected"))
    elif (
        "malicious_deployer" in flags or "repeat_deployer" in flags or "high_risk_deployer" in flags
    ):
        rows.append(("Deployer dump", _SCAM_SIGNAL_RED, "repeat / malicious deployer"))
    else:
        rows.append(("Deployer dump", _SCAM_SIGNAL_UNKNOWN, ""))

    # 8. Mint authority
    mint = _mint_auth(r)
    if mint is True:
        rows.append(("Mint authority", _SCAM_SIGNAL_RED, "supply can be minted"))
    elif mint is False:
        rows.append(("Mint authority", _SCAM_SIGNAL_GREEN, "renounced"))
    else:
        rows.append(("Mint authority", _SCAM_SIGNAL_UNKNOWN, ""))

    # 9. Freeze / blacklist authority
    freeze = _freeze_auth(r)
    if freeze is True:
        rows.append(("Freeze authority", _SCAM_SIGNAL_RED, "wallets can be frozen"))
    elif freeze is False:
        rows.append(("Freeze authority", _SCAM_SIGNAL_GREEN, "renounced"))
    else:
        rows.append(("Freeze authority", _SCAM_SIGNAL_UNKNOWN, ""))

    # 10. Tax manipulation (buy/sell/transfer)
    buy_tax = r.get("buy_tax") if r.get("buy_tax") is not None else sec.get("buy_tax")
    sell_tax = r.get("sell_tax") if r.get("sell_tax") is not None else sec.get("sell_tax")
    transfer_flag = sec.get("transfer_pausable") is True or sec.get("trading_cooldown") is True
    slippage_flag = sec.get("slippage_modifiable") is True

    def _tax_pct(value: Any) -> float | None:
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        return f * 100 if f <= 1 else f

    tax_high = any((p := _tax_pct(t)) is not None and p > 10 for t in (buy_tax, sell_tax))
    if tax_high or transfer_flag or slippage_flag:
        detail = (
            "transfer pausable" if sec.get("transfer_pausable") is True else "buy/sell tax >10%"
        )
        rows.append(("Tax manipulation", _SCAM_SIGNAL_RED, detail))
    elif buy_tax is not None or sell_tax is not None:
        rows.append(("Tax manipulation", _SCAM_SIGNAL_GREEN, "taxes within range"))
    else:
        rows.append(("Tax manipulation", _SCAM_SIGNAL_UNKNOWN, ""))

    # 11. Proxy / upgradeable
    proxy = sec.get("is_proxy")
    if proxy is True:
        rows.append(("Proxy / upgradeable", _SCAM_SIGNAL_RED, "logic can be swapped"))
    elif proxy is False:
        rows.append(("Proxy / upgradeable", _SCAM_SIGNAL_GREEN, "not a proxy"))
    else:
        rows.append(("Proxy / upgradeable", _SCAM_SIGNAL_UNKNOWN, ""))

    # 12. Owner-renounce lies (hidden owner / take-back / balance change)
    lie_flags = sec.get("can_take_back_ownership") is True or sec.get("hidden_owner") is True
    lie_flags = lie_flags or sec.get("owner_change_balance") is True
    if lie_flags:
        detail = (
            "hidden owner"
            if sec.get("hidden_owner") is True
            else "can take back ownership"
            if sec.get("can_take_back_ownership") is True
            else "owner changes balances"
        )
        rows.append(("Owner-renounce lies", _SCAM_SIGNAL_RED, detail))
    elif sec.get("owner_renounced") is True:
        rows.append(("Owner-renounce lies", _SCAM_SIGNAL_GREEN, "renounced"))
    else:
        rows.append(("Owner-renounce lies", _SCAM_SIGNAL_UNKNOWN, ""))

    # 13. Address poisoning / dusting
    dust = sec.get("dusting_detected")
    if dust is True:
        n = sec.get("dust_recipients")
        detail = f"{n} addresses dusted" if isinstance(n, (int, float)) and n else "dust campaign"
        rows.append(("Address poisoning / dusting", _SCAM_SIGNAL_RED, detail))
    elif dust is False:
        rows.append(("Address poisoning / dusting", _SCAM_SIGNAL_GREEN, "no dusting"))
    else:
        rows.append(("Address poisoning / dusting", _SCAM_SIGNAL_UNKNOWN, ""))

    # 14. Drainer contract (known scam / spam registry hit)
    rs = r.get("risk_signals")
    known_scammer = rs.get("known_scammer") if isinstance(rs, dict) else None
    if known_scammer is True or "flagged_spam" in flags or "known_scam" in flags:
        rows.append(("Drainer / known scam", _SCAM_SIGNAL_RED, "registry hit"))
    elif known_scammer is False:
        rows.append(("Drainer / known scam", _SCAM_SIGNAL_GREEN, "not in scam registry"))
    else:
        rows.append(("Drainer / known scam", _SCAM_SIGNAL_UNKNOWN, ""))

    # 15. Token-2022 (transfer hooks / close-mint / permanent delegate)
    token_2022 = _token_2022_flag(r)
    if token_2022:
        rows.append(("Token-2022 extensions", _SCAM_SIGNAL_RED, "hooks/freeze may hide risk"))
    else:
        rows.append(("Token-2022 extensions", _SCAM_SIGNAL_UNKNOWN, ""))

    # 16. Cloned contract (metadata clone of a known project)
    if "copycat_token" in flags or sec.get("is_copycat") is True:
        rows.append(("Cloned contract", _SCAM_SIGNAL_RED, "copycat / metadata clone"))
    elif sec.get("is_copycat") is False:
        rows.append(("Cloned contract", _SCAM_SIGNAL_GREEN, "not a clone"))
    else:
        rows.append(("Cloned contract", _SCAM_SIGNAL_UNKNOWN, ""))

    return rows


def _format_scam_signals_section(r: dict[str, Any]) -> list[str]:
    """Render the consolidated Scam Signals risk matrix — compact, honest.

    Signal-density design (2026-08 quality pass): flagged techniques render
    first WITH their evidence detail; checks that ran clean collapse into a
    single count line; techniques with no data collapse into one "no data"
    line listing their names (never claimed clean without a check). A
    16-row wall becomes a 2–8 line block where every remaining line carries
    information. Returns an empty list only for an empty report dict.
    """
    if not isinstance(r, dict) or not r:
        return []
    rows = _scam_signal_rows(r)
    flagged = [(label, detail) for label, state, detail in rows if state == _SCAM_SIGNAL_RED]
    clean = [label for label, state, _ in rows if state == _SCAM_SIGNAL_GREEN]
    unknown = [label for label, state, _ in rows if state == _SCAM_SIGNAL_UNKNOWN]

    lines = ["", "\U0001f50d <b>Scam Signals</b>", _THIN_SEP]
    for label, detail in flagged:
        suffix = f" \u2014 {html.escape(detail)}" if detail else ""
        lines.append(f"  {_SCAM_SIGNAL_RED} {html.escape(label)}{suffix}")
    if clean:
        lines.append(f"  {_SCAM_SIGNAL_GREEN} {len(clean)} checks passed clean")
    if unknown:
        # Telegram-width discipline: name at most 5, summarize the rest.
        shown = unknown[:5]
        rest = len(unknown) - len(shown)
        suffix = f" \u00b7 +{rest} more" if rest > 0 else ""
        lines.append(f"  \u26aa No data: {html.escape(' \u00b7 '.join(shown))}{suffix}")
    return lines
