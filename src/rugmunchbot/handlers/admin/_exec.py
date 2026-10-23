"""Subprocess execution seam for the admin commands.

Every command goes through :func:`_run` / :func:`_run_async` so the timeout,
argument shape (``shell=False``) and failure contract live in exactly one
place. Failures are reported as ``(returncode, message)`` and never raise.
"""

from __future__ import annotations

import asyncio
import subprocess

from rugmunchbot.handlers.admin._constants import _CMD_TIMEOUT
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.admin")


def _run(cmd: list[str]) -> tuple[int, str]:
    """Run a subprocess command with a 10s timeout.

    Returns ``(returncode, stdout+stderr)``. On timeout the process is killed
    and ``(124, "timeout")`` is returned. Only OSError (missing binary,
    permission denied, exec format) is swallowed; anything else is a bug.
    """
    try:
        proc = subprocess.run(  # noqa: S603 - fixed binaries, operator-controlled args, shell=False
            cmd,
            capture_output=True,
            text=True,
            timeout=_CMD_TIMEOUT,
            shell=False,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out.strip()
    except subprocess.TimeoutExpired:
        logger.warning("admin_cmd_timeout cmd=%s", " ".join(cmd)[:120])
        return 124, "Command timed out after 10s."
    except FileNotFoundError as exc:
        logger.warning("admin_cmd_missing_binary cmd=%s err=%s", " ".join(cmd)[:120], exc)
        return 127, f"Binary not found: {exc.filename}"
    except OSError as exc:
        logger.warning("admin_cmd_failed cmd=%s err=%s", " ".join(cmd)[:120], exc)
        return 1, f"Command failed: {exc}"


async def _run_async(cmd: list[str]) -> tuple[int, str]:
    """Run a subprocess command in the default executor, never blocking the loop."""
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _run, cmd)
    except (RuntimeError, OSError) as exc:
        logger.warning("admin_cmd_async_failed cmd=%s err=%s", " ".join(cmd)[:120], exc)
        return 1, f"Command failed: {exc}"
