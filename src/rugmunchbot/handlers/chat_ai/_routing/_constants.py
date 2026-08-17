"""Chat routing — endpoint/budget/deadline constants for the free-text pipeline."""

from __future__ import annotations

import os

from app.domains.telegram.rugmunchbot.config import BACKEND_URL

# ══════════════════════════════════════════════
# ENDPOINTS & BUDGETS — BACKEND / BRIDGE
# ══════════════════════════════════════════════
# Backend RAG chat (tier-1): CHAT_ENDPOINT / CHAT_TIMEOUT live in _llm.py
# next to _call_backend_chat and are re-exported by chat_ai/__init__.py.
SCAN_ENDPOINT = f"{BACKEND_URL}/api/v1/bot/scan"
SCAN_TIMEOUT = 6.0  # quick scan context is best-effort; never block chat on it
MAX_MESSAGE_CHARS = 2000  # backend ChatRequest.message limit

# Chat RAG Bridge — RugBrain/RugMunchScan context + SearXNG web search.
# One call returns live web search, RAG knowledge, market digest, scanner
# context and exploit rundown blocks (fail-open upstream).
BRIDGE_CONTEXT_ENDPOINT = os.getenv("BRIDGE_CONTEXT_ENDPOINT", "http://127.0.0.1:8095/chat/context")
BRIDGE_TIMEOUT = 12.0  # web search can take a few seconds; never block chat on it

# $TOKEN price card decision budget: after the AI answer is ready, the
# concurrently-running DexScreener lookup (fired by ai_chat_handler) gets
# this short window to land before we commit to the AI answer. The old flow
# serialized a 10s HTTP call before the pipeline even started.
TOKEN_CARD_DECIDE_WAIT = 1.0

# ONE hard end-to-end deadline around the whole chat turn. The inner stages
# each keep their own budgets (backend RAG 60s, local LLM 60s, tools ~8s+5s),
# but stacked worst-case they could hang a turn ~121s. This outer bound
# guarantees the user always gets SOME reply: asyncio.wait_for cancels the
# turn past the deadline and the timeout handler swaps the placeholder for a
# short graceful reply.
CHAT_TURN_DEADLINE = 25.0

# Long-tier (deep-analysis) turns get a SLIGHTLY higher end-to-end deadline
# than snappy/backend turns. The deep fallback chain stacks a hard-capped
# ~6s context gather (FALLBACK_CONTEXT_HARD_CAP) + a 15s long-model call
# (REMOTE_LONG_TIMEOUT) + a possible 10s paid fallback
# (llm_config._chat_once) — ~31s worst case, which no longer fits the snappy
# 25s window. Snappy/backend turns keep the fast CHAT_TURN_DEADLINE; only
# questions that _route_tier() sends to the "long" provider get the extra
# room. The typing indicator + interim "still digging" placeholder keep the
# user engaged for the longer wait (see _keep_typing / _edit_placeholder_if_slow).
CHAT_TURN_DEADLINE_LONG = 40.0

# Short graceful reply when a turn exceeds the hard end-to-end deadline.
TIMEOUT_REPLY = "\u23f3 Taking longer than expected \u2014 try again or narrow your question."
