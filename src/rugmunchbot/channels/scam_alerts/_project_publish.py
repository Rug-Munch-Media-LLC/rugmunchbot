"""Publish project-level scam alerts — DRY RUN BY DEFAULT.

Destination is the registry's ``shills`` role (@rmishills). Dry-run is the
default: nothing reaches Telegram unless ``RMI_SCAM_PROJECT_DRY_RUN=false``.
Dry runs write the rendered card to their own JSONL and use their own dedupe
ledger, so rehearsals never consume a real alert.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from rugmunchbot.support.logging import get_logger

from ._project_dedupe import alert_fingerprint, is_duplicate, ledger_path, record
from ._project_render import render_project_alert

logger = get_logger("rmi_bot.channels.scam_alerts.project")

DRYRUN_OUT = "/var/lib/rugmunchbot/scam_project_alert_dryrun.jsonl"
CHANNEL_ROLE = os.getenv("RMI_SCAM_PROJECT_CHANNEL_ROLE", "shills")


def dry_run_enabled(explicit: bool | None = None) -> bool:
    """Dry-run unless explicitly disabled — fail safe, not fail open."""
    if explicit is not None:
        return explicit
    return os.getenv("RMI_SCAM_PROJECT_DRY_RUN", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _write_dryrun(alert: dict[str, Any], post: str, out_path: Path) -> bool:
    """Persist the rehearsal card; unwritable path is a warning, not a failure."""
    try:
        path = out_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({**alert, "rendered": post}, sort_keys=True) + "\n")
        return True
    except OSError as exc:
        logger.warning("scam_project:dryrun_write_failed err=%s", str(exc)[:120])
        return False


async def publish_project_alert(
    bot: Any,
    alert: dict[str, Any] | None,
    *,
    chat_id: str | None = None,
    dry_run: bool | None = None,
    ledger: Path | None = None,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Publish one project alert; returns ``{"status": ...}``.

    Statuses: ``skipped_no_alert`` (nothing earned), ``duplicate``,
    ``dry_run``, ``posted``, ``send_failed``, ``no_channel``.
    """
    if not alert:
        return {"status": "skipped_no_alert"}

    dry = dry_run_enabled(dry_run)
    post = render_project_alert(alert)
    fingerprint = alert_fingerprint(alert)
    ledger_file = ledger or ledger_path(dry)
    if is_duplicate(fingerprint, ledger_file):
        logger.info("scam_project:duplicate fp=%s", fingerprint[:12])
        return {"status": "duplicate", "fingerprint": fingerprint}

    if dry:
        wrote = _write_dryrun(
            alert, post, out_path or Path(os.getenv("RMI_SCAM_PROJECT_DRYRUN_OUT", DRYRUN_OUT))
        )
        record(fingerprint, alert, ledger_file)
        logger.info(
            "scam_project:dry_run fp=%s severity=%s wrote=%s",
            fingerprint[:12],
            alert.get("severity"),
            wrote,
        )
        return {"status": "dry_run", "fingerprint": fingerprint, "post": post, "written": wrote}

    if chat_id is None:
        from rugmunchbot.channels.scheduler._core import _chat_id

        chat_id = _chat_id(CHANNEL_ROLE)
    if not chat_id:
        logger.warning("scam_project:no_channel role=%s", CHANNEL_ROLE)
        return {"status": "no_channel", "fingerprint": fingerprint, "post": post}

    try:
        from telegram.constants import ParseMode

        from rugmunchbot.channels.scheduler._core import _safe_send

        sent = bool(
            await _safe_send(
                bot,
                chat_id,
                post,
                parse_mode=ParseMode.HTML,
                content_type="scam_alert",
            )
        )
    except Exception as exc:  # a transport bug must not crash the scheduler
        logger.error("scam_project:send_error err=%s", str(exc)[:200])
        sent = False

    if sent:
        record(fingerprint, alert, ledger_file)
        logger.info(
            "scam_project:posted fp=%s severity=%s", fingerprint[:12], alert.get("severity")
        )
        return {"status": "posted", "fingerprint": fingerprint}
    logger.warning("scam_project:send_failed fp=%s", fingerprint[:12])
    return {"status": "send_failed", "fingerprint": fingerprint, "post": post}
