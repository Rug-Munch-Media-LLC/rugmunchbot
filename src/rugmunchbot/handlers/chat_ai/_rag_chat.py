"""Canonical RAG chat client — the bot's ONE model path.

Every free-text / ``/chat`` / ``/ask`` turn is answered by the backend knowledge
desk at ``POST /api/v1/chat`` (auto-RAG + live tools). This module is the whole
client: request shaping, a 45 s per-message cap, payload parsing, citation
links, cached footer. Failure contract: timeout / 5xx returns ``None`` (or the
payload's retrieval-only answer); the caller replies CATCHING_UP_REPLY.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from rugmunchbot.config import BACKEND_URL
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.rag_chat")

#: Canonical knowledge-desk endpoint (backend-owned, loopback on Talos).
CHAT_ENDPOINT = os.getenv("RAG_CHAT_ENDPOINT", f"{BACKEND_URL}/api/v1/chat")

#: ONE cap per message, end to end (task contract: 45 s).
CHAT_TIMEOUT = 45.0

MAX_QUESTION_CHARS = 2000  # endpoint caps ``message`` at 2000 chars; clamp, no 422s

#: Graceful degradation — short, honest, points at /scan.
CATCHING_UP_REPLY = (
    "\U0001f9e0 The knowledge desk is catching up \u2014 that one didn't get "
    "through.\n\nTry again in a moment, or run <code>/scan &lt;address&gt;</code> "
    "for live on-chain intel right now."
)
#: Footer shown ONLY when the desk served cached knowledge (fallback_used).
CACHED_FOOTER = "\u2139\ufe0f cached knowledge"
_SECURITY_WORDS = frozenset({"rug", "scam", "honeypot", "exploit", "drain", "phish", "hack"})
_MARKET_WORDS = frozenset({"price", "market", "chart", "buy", "sell", "long", "short", "volume"})


@dataclass(frozen=True)
class RagAnswer:
    """One parsed knowledge-desk answer (provider-agnostic, UI-ready)."""

    text: str
    citations: tuple[tuple[str, str], ...] = ()
    provider: str = ""
    model: str = ""
    fallback_used: bool = False
    refused: bool = False
    retrieval_only: bool = False
    rag_count: int = 0

    def as_result(self) -> dict[str, Any]:
        """Shape the pipeline's ``result`` dict (delivery + trace contract)."""
        return {
            "message": render_answer(self),
            "provider": "backend",
            "model": self.model,
            "rag_count": self.rag_count,
            "refused": self.refused,
            "fallback_used": self.fallback_used,
        }


def infer_persona(text: str) -> str:
    """Cheap persona inference; ``desk`` is the safe default."""
    words = {w.strip(".,!?:;()[]{}\"'").lower() for w in text.split()}
    if words & _SECURITY_WORDS:
        return "security"
    if words & _MARKET_WORDS:
        return "market"
    return "desk"


def _citations(raw: Any) -> tuple[tuple[str, str], ...]:
    """Accept ``[{title,url}]`` / ``[{source,link}]`` / ``["url"]``."""
    if not isinstance(raw, list):
        return ()
    out: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, str) and item.startswith("http"):
            out.append((item, item))
        elif isinstance(item, dict):
            url = str(item.get("url") or item.get("link") or item.get("href") or "").strip()
            if url.startswith("http"):
                title = str(item.get("title") or item.get("source") or item.get("name") or url)
                out.append((title.strip()[:120], url))
    return tuple(out[:5])


def _looks_refused(data: dict[str, Any], text: str) -> bool:
    if bool(data.get("refused") or data.get("is_refusal")):
        return True
    if str(data.get("answer_type") or data.get("type") or "").lower() in {"refusal", "declined"}:
        return True
    head = text.strip().lower()[:160]
    return head.startswith(("i can't", "i cannot", "i won't", "i'm not able", "i am not able"))


def parse_answer(data: Any, *, retrieval_only: bool = False) -> RagAnswer | None:
    """Parse the desk payload; ``None`` when it carries no answer text."""
    if not isinstance(data, dict):
        return None
    text = str(data.get("message") or data.get("answer") or data.get("content") or "").strip()
    if not text:
        return None
    try:
        rag_count = int(data.get("x_rag_context") or data.get("rag_count") or 0)
    except (TypeError, ValueError):
        rag_count = 0
    return RagAnswer(
        text=text,
        citations=_citations(
            data.get("citations") or data.get("sources") or data.get("references")
        ),
        provider=str(data.get("provider") or ""),
        model=str(data.get("model") or ""),
        fallback_used=bool(data.get("fallback_used")),
        refused=_looks_refused(data, text),
        retrieval_only=retrieval_only or bool(data.get("retrieval_only")),
        rag_count=rag_count,
    )


def render_answer(answer: RagAnswer) -> str:
    """User-visible body: answer + citation links + compact cached footer.

    Citations stay real links (markdown — the delivery layer converts them to
    Telegram HTML). The provider name never appears unless the desk served
    cached knowledge, and then only as the two-word footer.
    """
    body = answer.text.strip()
    if answer.citations:
        links = "\n".join(f"\u2022 [{t}]({u})" for t, u in answer.citations)
        body = f"{body}\n\n\U0001f4da <b>Sources</b>\n{links}"
    if answer.fallback_used:
        body = f"{body}\n\n{CACHED_FOOTER}"
    return body


def build_payload(
    text: str,
    *,
    user_id: int | str,
    persona: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Request body for ``POST /api/v1/chat`` (channel-tagged)."""
    payload: dict[str, Any] = {
        "message": text.strip()[:MAX_QUESTION_CHARS],
        "channel": "telegram",
        "user_id": str(user_id),
        "persona": persona or infer_persona(text),
    }
    if history:
        payload["history"] = [
            {"role": str(m.get("role", "user")), "content": str(m.get("content", ""))[:2000]}
            for m in history[-6:]
            if isinstance(m, dict) and m.get("content")
        ]
    return payload


async def ask_rag_chat(
    text: str,
    *,
    user_id: int | str,
    persona: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> RagAnswer | None:
    """Ask the canonical knowledge desk. Never raises — ``None`` on any failure."""
    if not text.strip():
        return None
    payload = build_payload(text, user_id=user_id, persona=persona, history=history)
    try:
        async with httpx.AsyncClient(timeout=CHAT_TIMEOUT) as client:
            response = await client.post(CHAT_ENDPOINT, json=payload)
        if response.status_code >= 400:
            logger.warning("rag_chat:desk_error status=%s user=%s", response.status_code, user_id)
            try:
                return parse_answer(response.json(), retrieval_only=True)
            except ValueError:
                return None
        return parse_answer(response.json())
    except (httpx.TimeoutException, httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "rag_chat:desk_unreachable user=%s error=%s endpoint=%s",
            user_id,
            type(exc).__name__,
            CHAT_ENDPOINT,
        )
        return None
    except Exception:  # a chat turn must never crash the poll loop
        logger.exception("rag_chat:desk_unexpected user=%s", user_id)
        return None
