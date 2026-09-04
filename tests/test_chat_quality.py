"""Tests for chat AI response quality."""

from __future__ import annotations

from rugmunchbot.handlers.chat_ai._routing._constants import (
    CHAT_TURN_DEADLINE,
    MAX_MESSAGE_CHARS,
    SCAN_ENDPOINT,
    TIMEOUT_REPLY,
)


class TestChatRoutingQuality:
    """Tests for chat routing quality."""

    def test_scan_endpoint_configured(self):
        assert "scan" in SCAN_ENDPOINT.lower()

    def test_max_message_chars_reasonable(self):
        assert 1000 <= MAX_MESSAGE_CHARS <= 4096

    def test_chat_turn_deadline_reasonable(self):
        # Raised 35s -> 70s (2026-09-04) so RAG + live tools + guardrail can
        # finish a real market-analysis turn instead of dead-air timing out.
        assert 10.0 <= CHAT_TURN_DEADLINE <= 90.0

    def test_timeout_reply_exists(self):
        assert isinstance(TIMEOUT_REPLY, str)
        assert len(TIMEOUT_REPLY) > 10


class TestChatAIFallbacks:
    """Tests for chat AI fallback behavior."""

    def test_fallback_message_exists(self):
        from rugmunchbot.handlers.chat_ai._routing._core import _FALLBACK_MESSAGE

        assert isinstance(_FALLBACK_MESSAGE, str)
        assert len(_FALLBACK_MESSAGE) > 20
