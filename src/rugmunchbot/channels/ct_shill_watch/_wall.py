"""ct_shill_watch — wall-of-shame record/update + rescore (idempotent)."""

from __future__ import annotations

import json
from typing import Any

from rugmunchbot.support.logging import get_logger

from . import _config
from ._score import score_kol

logger = get_logger("rmi_bot.channels.ct_shill_watch")


def _norm_handle(handle: Any) -> str:
    return str(handle or "").strip().lstrip("@").lower()


def _evidence_bundle(
    tweet_text: str, tweet_url: str, screenshot_url: str, platform: str
) -> dict[str, str]:
    bundle: dict[str, str] = {}
    if tweet_text:
        bundle["tweet_text"] = str(tweet_text)[:600]
    if tweet_url:
        bundle["tweet_url"] = str(tweet_url)[:240]
    if screenshot_url:
        bundle["screenshot_url"] = str(screenshot_url)[:400]
    if platform:
        bundle["platform"] = platform
    return bundle


def _load_shame_entry(handle: str, wall_path: str | None = None) -> dict[str, Any] | None:
    try:
        data = json.loads(
            open(wall_path or _config.WALL_OF_SHAME_PATH, encoding="utf-8").read()  # noqa: SIM115
        )
        h = _norm_handle(handle)
        return next(
            (e for e in data.get("wall_of_shame", []) if _norm_handle(e.get("handle")) == h),
            None,
        )
    except (OSError, ValueError):
        return None


def record_shill(
    handle: str,
    *,
    handle_id: str = "",
    token: str = "",
    tweet_text: str = "",
    tweet_url: str = "",
    screenshot_url: str = "",
    wallet: str = "",
    coordinated: bool = False,
    platform: str = "x",
    wall_path: str | None = None,
) -> bool:
    """Add/update one shill in ``wall_of_shame.json`` (idempotent per handle).

    New entries carry the full evidence bundle (tweet text + screenshot URL +
    shill_count / tokens_shilled / last_shill_ts / platforms). Existing
    entries get shill_count incremented, the token appended, and severity
    escalated when coordinated (X + TG). Returns True when the file changed.
    Never raises.
    """
    path = wall_path or _config.WALL_OF_SHAME_PATH
    h = _norm_handle(handle)
    if not h:
        return False
    try:
        data = json.loads(open(path, encoding="utf-8").read())  # noqa: SIM115
    except (OSError, ValueError) as exc:
        logger.debug("ct_shill:shame_load_failed err=%s", str(exc)[:120])
        return False
    wall = data.get("wall_of_shame", []) if isinstance(data, dict) else []
    entry = next(
        (
            e
            for e in wall
            if _norm_handle(e.get("handle")) == h or _norm_handle(e.get("handle_id")) == h
        ),
        None,
    )
    changed = False
    if entry is None:
        entry = {
            "handle": h,
            "handle_id": handle_id or h,
            "wallet": wallet or "",
            "wallets": [wallet] if wallet else [],
            "category": "shill",
            "severity": "high" if coordinated else "medium",
            "risk_level": "critical" if coordinated else "high",
            "group": "",
            "evidence_url": tweet_url,
            "evidence": [_evidence_bundle(tweet_text, tweet_url, screenshot_url, platform)],
            "shill_count": 1,
            "last_shill_ts": _config.now_iso(),
            "tokens_shilled": [token] if token else [],
            "platforms": [platform] if platform else [],
            "coordinated": coordinated,
            "source": "ct_shill_watch",
        }
        wall.append(entry)
        changed = True
    else:
        entry["shill_count"] = int(entry.get("shill_count") or 0) + 1
        entry["last_shill_ts"] = _config.now_iso()
        tokens = entry.setdefault("tokens_shilled", [])
        if token and token not in tokens:
            tokens.append(token)
            changed = True
        platforms = entry.setdefault("platforms", [])
        if platform and platform not in platforms:
            platforms.append(platform)
            changed = True
        if coordinated and not entry.get("coordinated"):
            entry["coordinated"] = True
            entry["severity"] = "high"
            entry["risk_level"] = "critical"
            changed = True
        if tweet_url and tweet_url != entry.get("evidence_url"):
            entry["evidence_url"] = tweet_url
            changed = True
        ev = entry.setdefault("evidence", [])
        bundle = _evidence_bundle(tweet_text, tweet_url, screenshot_url, platform)
        if bundle and bundle not in ev:
            ev.append(bundle)
            changed = True
        if wallet and not entry.get("wallet"):
            entry["wallet"] = wallet
            entry.setdefault("wallets", []).append(wallet)
            changed = True
    if changed:
        try:
            out = {"updated": _config.now_iso(), "count": len(wall), "wall_of_shame": wall}
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(out, fh, indent=2, ensure_ascii=False)
        except OSError as exc:
            logger.warning("ct_shill:shame_write_failed err=%s", str(exc)[:120])
            return False
        logger.info(
            "ct_shill:shame_recorded handle=%s token=%s coordinated=%s", h, token, coordinated
        )
    return changed


def rescore_wall_of_shame(wall_path: str | None = None) -> int:
    """Recompute severity for every wall entry from its shill history."""
    path = wall_path or _config.WALL_OF_SHAME_PATH
    try:
        data = json.loads(open(path, encoding="utf-8").read())  # noqa: SIM115
    except (OSError, ValueError) as exc:
        logger.debug("ct_shill:rescore_load_failed err=%s", str(exc)[:120])
        return 0
    wall = data.get("wall_of_shame", []) if isinstance(data, dict) else []
    changed = 0
    for entry in wall:
        if entry.get("source") != "ct_shill_watch" and not entry.get("shill_count"):
            continue  # only rescore entries this pipeline owns/annotates
        score = score_kol(entry)
        entry["shill_score"] = score
        if score >= 6:
            sev, risk = "high", "critical"
        elif score >= 2:
            sev, risk = "medium", "high"
        else:
            sev, risk = "low", "watch"
        if (entry.get("severity"), entry.get("risk_level")) != (sev, risk) and entry.get(
            "source"
        ) == "ct_shill_watch":
            entry["severity"], entry["risk_level"] = sev, risk
            changed += 1
    if changed:
        try:
            out = {"updated": _config.now_iso(), "count": len(wall), "wall_of_shame": wall}
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(out, fh, indent=2, ensure_ascii=False)
        except OSError as exc:
            logger.warning("ct_shill:rescore_write_failed err=%s", str(exc)[:120])
            return 0
    return changed
