"""ct_shill_watch — shill confirmation (scanner + Telegram cross-reference)."""

from __future__ import annotations

from rugmunchbot.support.logging import get_logger

from ._wall import _norm_handle

logger = get_logger("rmi_bot.channels.ct_shill_watch")


async def tg_cross_ref(handle: str) -> bool:
    """True when the backend shill store already has TELEGRAM evidence for
    this handle → the account shills on BOTH platforms → coordinated."""
    try:
        from rugmunchbot.services import shill_api

        data = await shill_api.get_evidence(account=_norm_handle(handle), limit=50)
        rows = (data or {}).get("evidence") or (data or {}).get("rows") or []
        return any(
            str(r.get("source") or "").lower() == "telegram" for r in rows if isinstance(r, dict)
        )
    except Exception as exc:
        logger.debug("ct_shill:tg_crossref_failed err=%s", str(exc)[:120])
        return False


async def confirm_shill(handle: str, token: str, chain: str) -> tuple[bool, bool]:
    """(confirmed, coordinated) for one (handle, token) promotion.

    Confirmed when the canonical scanner flags the token HIGH/CRITICAL
    (honeypot / rug pattern / LP pull) OR the handle already has TG shill
    evidence (cross-platform repeat offender). Coordinated = TG cross-hit.
    Fail-open (False, False).
    """
    coordinated = await tg_cross_ref(handle)
    if coordinated:
        return True, True
    try:
        from rugmunchbot.channels.kol_discovery import _is_risky_token

        if await _is_risky_token(token, chain):
            return True, False
    except Exception as exc:
        logger.debug("ct_shill:risky_check_failed err=%s", str(exc)[:120])
    return False, False
