"""Chat routing — endpoint/budget/deadline constants for the free-text pipeline."""

from __future__ import annotations

import os

from rugmunchbot.config import BACKEND_URL

# ══════════════════════════════════════════════
# ENDPOINTS & BUDGETS — BACKEND SCANNER / BRIDGE
# ══════════════════════════════════════════════
# RugMunchScan is its OWN product (backend-owned): the bot CONSUMES the
# quick-scan endpoint for chat context, it does not re-implement the
# scanner. Best-effort; never block chat on it.
SCAN_ENDPOINT = f"{BACKEND_URL}/api/v1/bot/scan"
SCAN_TIMEOUT = 6.0  # quick scan context is best-effort; never block chat on it
MAX_MESSAGE_CHARS = 2000  # question window for the remote LLM prompt

# Chat RAG Bridge — optional local context layer (SearXNG web search +
# market digest + exploit rundown). Fail-open: when the bridge is down the
# bot answers from its own RAG lookup + live tools + daily digest.
BRIDGE_CONTEXT_ENDPOINT = os.getenv("BRIDGE_CONTEXT_ENDPOINT", "http://127.0.0.1:8095/chat/context")
BRIDGE_TIMEOUT = 12.0  # web search can take a few seconds; never block chat on it

# $TOKEN price card decision budget: after the AI answer is ready, the
# concurrently-running DexScreener lookup (fired by ai_chat_handler) gets
# this short window to land before we commit to the AI answer. The old flow
# serialized a 10s HTTP call before the pipeline even started.
TOKEN_CARD_DECIDE_WAIT = 1.0

# ONE hard end-to-end deadline around the whole chat turn. The inner stages
# each keep their own budgets (tools ~8s+5s, remote LLM snappy 20s / long 35s),
# but stacked worst-case they could hang a turn. This outer bound guarantees
# the user always gets SOME reply: asyncio.wait_for cancels the turn past the
# deadline and the timeout handler swaps the placeholder for a short graceful
# reply. 70s = 25s snappy LLM generation (REMOTE_SNAPPY_TIMEOUT, raised with
# REMOTE_SNAPPY_MAX_TOKENS so a full 2000-token answer completes) + the
# concurrent context gather that overlaps the LLM call, with headroom for
# RAG + live tools + guardrail so a real market-analysis turn can finish
# instead of dead-air timing out.
CHAT_TURN_DEADLINE = 70.0

# Long-tier (deep-analysis) turns get a HIGHER end-to-end deadline than
# snappy/backend turns. The deep fallback chain stacks a hard-capped
# ~6s context gather (FALLBACK_CONTEXT_HARD_CAP) + a 35s long-model call
# (REMOTE_LONG_TIMEOUT — raised with REMOTE_LONG_MAX_TOKENS so the 3000-token
# deep-analysis budget can complete on hosted GPU) + a possible 20s paid
# fallback (llm_config._chat_once) — ~55s worst case, which no longer fits
# the snappy 30s window. Snappy/backend turns keep the fast CHAT_TURN_DEADLINE;
# only questions that _route_tier() sends to the "long" provider get the
# extra room. The typing indicator + interim "still digging" placeholder keep
# the user engaged for the longer wait (see _keep_typing / _edit_placeholder_if_slow).
# W11 (2026-08-25): 60s produced dead-air timeouts (trace: ok=false
# latency=60003). 90s gives RAG + the long-model call + paid fallback real
# room to land a full market-analysis answer instead of timing out and
# returning the "needs more digging" fallback.
CHAT_TURN_DEADLINE_LONG = 90.0

# Short graceful reply when a turn exceeds the hard end-to-end deadline.
# This is NOT a failure — the pipeline keeps running in the background and
# the answer may still land. So we tell the user it's still working rather
# than the "needs more digging" fallback (which is reserved for real errors,
# see ERROR_REPLY).
TIMEOUT_REPLY = (
    "\u23f3 Still working on it \u2014 this one's taking a while.\n\n"
    "I'll send the answer as soon as it's ready. You can keep chatting in the meantime."
)

# Reply when a turn genuinely FAILS (an unexpected exception in the pipeline),
# as opposed to merely exceeding the deadline. This is the "needs more digging"
# fallback — it only fires on actual errors, never on a slow-but-still-running
# timeout.
ERROR_REPLY = (
    "\u23f3 That one needs more digging than I can finish right now.\n\n"
    "Try narrowing it (one token / one event), or ask me to /scan a specific address."
)
