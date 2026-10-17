"""Regression: the news-rerank lane resolves RAG credentials through the
canonical ``tools.rag_lookup`` resolver (the Daily Rug blocker, 2026-10-17).

``channels._news_rerank._rag_api_key()`` read ONLY
``os.getenv("RMI_RAG_API_KEY", "")`` with no secret-file fallback. The running
systemd unit's ``Environment=`` carries only ``RAG_API_URL``, so the guard in
``fetch_top_ranked_stories`` fired on every run
(``news_rerank:no_api_config skipped``) and the Daily Rug published with zero
ranked stories. ``rag_lookup._rag_api_key()`` has the full chain
(env → on-disk secret file → secrets manager) and works.

The contract asserted here is DELEGATION, not value: any local
re-implementation of the resolver in ``_news_rerank`` can never silently
regress the lane again, because it must return exactly what the canonical
resolver returns.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
import rugmunchbot.channels._news_rerank as nr

from rugmunchbot.channels import _news_rerank
from rugmunchbot.tools import rag_lookup


def test_rag_api_key_delegates_to_rag_lookup():
    assert _news_rerank._rag_api_key() == rag_lookup._rag_api_key()


def test_rag_api_url_delegates_to_rag_lookup():
    assert _news_rerank._rag_api_url() == rag_lookup._rag_api_url()


def test_rag_api_key_resolves_without_the_env_var():
    """The lane must stay alive in a context that does not source the bot env.

    The bug: no ``RMI_RAG_API_KEY`` in the unit's ``Environment=`` and no
    secret-file fallback → empty key → the whole ranked lane skipped. Both
    resolvers must therefore produce a non-empty key from the secret chain
    (on-disk secret file → secrets manager), never only from the env var.
    """
    key = _news_rerank._rag_api_key()
    assert key
    assert key == rag_lookup._rag_api_key()


class TestWindow:
    def test_morning_window_uses_midday_last_run(self) -> None:
        fake = MagicMock()
        fake.last_run.side_effect = lambda job: (
            datetime.now(UTC) - timedelta(hours=5, minutes=30)
            if job == "channel_midday_update"
            else None
        )
        with patch("rugmunchbot.channels.scheduler_state.get_state", return_value=fake):
            assert 5.0 <= nr.morning_window_hours() <= 6.0

    def test_midday_window_uses_morning_last_run(self) -> None:
        fake = MagicMock()
        fake.last_run.side_effect = lambda job: (
            datetime.now(UTC) - timedelta(hours=2) if job == "channel_morning_rug" else None
        )
        with patch("rugmunchbot.channels.scheduler_state.get_state", return_value=fake):
            assert 1.5 <= nr.midday_window_hours() <= 2.5

    def test_fallback_12h_when_never_run(self) -> None:
        fake = MagicMock()
        fake.last_run.return_value = None
        with patch("rugmunchbot.channels.scheduler_state.get_state", return_value=fake):
            assert nr.morning_window_hours() == 12.0
            assert nr.midday_window_hours() == 12.0

    def test_window_clamped_to_max(self) -> None:
        fake = MagicMock()
        fake.last_run.return_value = datetime.now(UTC) - timedelta(days=10)
        with patch("rugmunchbot.channels.scheduler_state.get_state", return_value=fake):
            assert nr.window_hours_since(("channel_morning_rug",)) == nr.MAX_WINDOW_HOURS

    def test_window_fail_open_on_state_error(self) -> None:
        with patch(
            "rugmunchbot.channels.scheduler_state.get_state", side_effect=RuntimeError("boom")
        ):
            assert nr.window_hours_since(("x",)) == 12.0

# ── HTTP lane ───────────────────────────────────────────────────────
