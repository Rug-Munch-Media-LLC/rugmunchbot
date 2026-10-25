"""ALPHA-channel repeating jobs — split out of ``register_channel_jobs``.

Funding dislocation, whale moves, social-velocity spikes and the daily perps
recap are all ALPHA content (CONTENT-STRATEGY-CANONICAL): data-driven market
signals that never belong on @rmialerts. One private registrar per cadence so
each tick reads in one place and every function stays inside the structure
gate. Gating flags arrive as arguments — ``_register`` owns them, so a test
patches exactly one owner.
"""

from __future__ import annotations

import os
from datetime import UTC
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels.scheduler import _core
from rugmunchbot.channels.scheduler._posts_dislocation import build_dislocation_post
from rugmunchbot.channels.scheduler._posts_social_spikes import build_social_spike_post
from rugmunchbot.channels.scheduler._tick import _tick_wrapper

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = _core.logger


def _register_dislocation(job_queue: Any) -> None:
    """Cross-venue funding basis finds, every ``DISLOCATION_ALERT_INTERVAL_H``."""

    async def _dislo_tick(ctx: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            text = await build_dislocation_post()
            if text and _core._chat_id("alpha"):
                from rugmunchbot.channels.scheduler._core import _post_photo_aware, _safe_send

                await _post_photo_aware(
                    ctx.bot, _core._chat_id("alpha"), text, _safe_send, content_type="alpha"
                )
        except Exception as exc:
            logger.warning("dislocation_tick failed: %s", str(exc)[:120])

    job_queue.run_repeating(
        _tick_wrapper("channel_dislocation", _dislo_tick, envelope=True),
        interval=int(os.getenv("DISLOCATION_ALERT_INTERVAL_H", "2")) * 3600,
        first=600,
        name="channel_dislocation",
    )


def _register_whales(job_queue: Any) -> None:
    """Multi-chain whale moves, quality-gated because ALPHA is paid content."""

    from rugmunchbot.channels.scheduler._posts_whales import build_whale_post
    from rugmunchbot.support.image_policy import resolve_post_image
    from rugmunchbot.support.quality_gate import pre_post_quality_gate

    async def _whale_tick(ctx: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            text = await build_whale_post()
            alpha_chat = _core._chat_id("alpha")
            if not (text and alpha_chat):
                return
            resolved_image = await resolve_post_image("alpha", logger=logger)
            gate = await pre_post_quality_gate(
                text,
                image_url=resolved_image,
                source="whale_alerts",
                kind="whale_alert",
                min_words=8,  # structured alert format — terse by design
                require_image=True,
                allow_placeholder_image=True,
                logger=logger,
            )
            if not gate.ok:
                logger.warning(
                    "whale_alerts_tick blocked by quality gate: %s",
                    [v.rule for v in gate.violations],
                )
                return
            from rugmunchbot.channels.scheduler._core import _post_photo_aware, _safe_send

            await _post_photo_aware(
                ctx.bot, alpha_chat, gate.text, _safe_send, content_type="whale"
            )
        except Exception as exc:
            logger.warning("whale_alerts_tick failed: %s", str(exc)[:120])

    job_queue.run_repeating(
        _tick_wrapper("channel_whale_alerts", _whale_tick, envelope=True),
        interval=int(os.getenv("WHALE_ALERT_INTERVAL_MIN", "30")) * 60,
        first=180,
        name="channel_whale_alerts",
    )


def _register_social_spikes(job_queue: Any) -> None:
    """Social-velocity spikes — narrative formation, every ``SOCIAL_SPIKE_INTERVAL_MIN``."""

    async def _social_tick(ctx: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            text = await build_social_spike_post()
            if text and _core._chat_id("alpha"):
                from rugmunchbot.channels.scheduler._core import _post_photo_aware, _safe_send

                await _post_photo_aware(
                    ctx.bot, _core._chat_id("alpha"), text, _safe_send, content_type="alpha"
                )
        except Exception as exc:
            logger.warning("social_spike_tick failed: %s", str(exc)[:120])

    job_queue.run_repeating(
        _tick_wrapper("channel_social_spikes", _social_tick, envelope=True),
        interval=int(os.getenv("SOCIAL_SPIKE_INTERVAL_MIN", "90")) * 60,
        first=900,
        name="channel_social_spikes",
    )


def _register_perps_recap(job_queue: Any) -> None:
    """Daily perps desk recap (funding / OI / liquidations) on ALPHA."""
    from datetime import time as _perps_time

    from rugmunchbot.channels.perps_recap import build_perps_recap_post

    async def _perps_recap_tick(ctx: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            text = await build_perps_recap_post()
            if text and _core._chat_id("alpha"):
                from rugmunchbot.channels.scheduler._core import _post_photo_aware, _safe_send

                await _post_photo_aware(
                    ctx.bot, _core._chat_id("alpha"), text, _safe_send, content_type="alpha"
                )
        except Exception as exc:
            logger.warning("perps_recap_tick failed: %s", str(exc)[:120])

    job_queue.run_daily(
        _tick_wrapper("channel_perps_recap", _perps_recap_tick, envelope=True),
        time=_perps_time(
            hour=int(os.getenv("RMI_PERPS_RECAP_HOUR_UTC", "7")), minute=0, tzinfo=UTC
        ),
        name="channel_perps_recap",
    )


def register_alpha_jobs(job_queue: Any, *, alpha_enabled: bool) -> None:
    """Register the ALPHA rails when the channel flag and chat id are both set."""
    if not alpha_enabled or not _core._chat_id("alpha"):
        return
    repeating = hasattr(job_queue, "run_repeating")
    if repeating:
        _register_dislocation(job_queue)
        _register_whales(job_queue)
        _register_social_spikes(job_queue)
    if hasattr(job_queue, "run_daily") and os.getenv("RMI_PERPS_RECAP_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    ):
        _register_perps_recap(job_queue)
