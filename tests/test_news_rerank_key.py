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
