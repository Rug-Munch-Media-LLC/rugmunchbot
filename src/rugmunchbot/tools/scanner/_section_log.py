"""Scan-section fallback audit logging (SCAN-QUALITY-PLAN item 3).

Every time a section's data source fails over to the next leg, one line:

    scan_section_fallback section=<name> from=<source> to=<source> reason=<why>

lands in the log so completeness is auditable after the fact ("why did this
card say holders unavailable?" → grep). Pure logging helper — never raises,
never affects scan flow.
"""

from __future__ import annotations

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.scanner.sections")


def log_section_fallback(
    section: str,
    from_src: str,
    to_src: str,
    *,
    reason: str = "",
) -> None:
    """Record one section failover (info level — always auditable).

    ``to_src`` may be ``"none"`` when every leg failed and the card will
    render the honest-missing marker instead of data.
    """
    try:
        logger.info(
            "scan_section_fallback section=%s from=%s to=%s%s",
            section,
            from_src,
            to_src,
            f" reason={reason}" if reason else "",
        )
    except Exception as exc:
        logger.debug("section_fallback_log_failed err=%s", str(exc)[:80])


__all__ = ["log_section_fallback"]
