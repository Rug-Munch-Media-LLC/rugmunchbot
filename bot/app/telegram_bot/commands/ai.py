"""
🧠 RMI Crypto AI Agent — DataBus-Powered
==========================================
THE consumer-facing AI for Telegram and web. All data flows through DataBus.

Architecture:
  User Query → Sanitize → Intent Detection → DataBus.fetch() → RAG → DeepSeek → Reply
                              ↓                    ↓            ↓
                         price/security/     93 chains    3,480 docs
                         trending/news       116 providers  knowledge base

Single source of truth: DataBus (cache, dedup, credit-aware, fallback chains)
"""

import json
import logging
import os
import re
from datetime import UTC, datetime
from urllib.request import Request, urlopen

logger = logging.getLogger("rmi.agent")

DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
BACKEND_URL = os.getenv("BACKEND_URL", "http://rmi-backend:8000")
MODEL = "deepseek-v4-flash"

# ── Security ──
INJECTION_PATTERNS = [
    r"ignore (all |previous |above )?(instructions|prompts|rules)",
    r"you are now",
    r"system:\s*",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"\[INST\]",
    r"\[/INST\]",
    r"DAN\s",
    r"jailbreak",
    r"prompt\s*injection",
]
PRIVATE_KEY_RX = re.compile(
    r"(0x[a-fA-F0-9]{64}|[1-9A-HJ-NP-Za-km-z]{44,88}|sk-[a-zA-Z0-9]{32,}|-----BEGIN.*PRIVATE KEY-----)"
)

# ── DataBus Chains available to the bot ──
AGENT_CHAINS = {
    "token_price": "Real-time price from CoinGecko/DexScreener consensus",
    "token_security": "42 security checks via GoPlus API",
    "trending": "Trending tokens across all chains",
    "token_launches": "New token launches with age risk",
    "market_overview": "Global crypto market metrics + Fear & Greed",
    "news": "Aggregated crypto news from 200+ RSS sources",
    "fear_greed": "Market sentiment index 0-100",
    "live_prices": "Multi-coin price lookup",
}


def _databus_fetch(data_type: str, **params) -> dict | None:
    """Fetch data from DataBus — single source of truth."""
    try:
        payload = json.dumps({"data_type": data_type, **params}).encode()
        req = Request(
            f"{BACKEND_URL}/api/v1/databus/fetch",
            data=payload,
            headers={"Content-Type": "application/json", "X-RMI-Key": "rmi-internal-2026"},
        )
        resp = urlopen(req, timeout=8)
        return json.loads(resp.read())
    except Exception as e:
        logger.warning(f"DataBus.{data_type}: {e}")
        return None


def _rag_search(query: str, limit: int = 3) -> str:
    """Query RAG knowledge base."""
    try:
        req = Request(
            f"{BACKEND_URL}/api/v1/rag/search?q={query}&limit={limit}",
            headers={"X-RMI-Key": "rmi-internal-2026"},
        )
        resp = urlopen(req, timeout=5)
        data = json.loads(resp.read())
        docs = data.get("results", [])
        return "\n".join(f"• {d.get('content', '')[:200]}" for d in docs[:limit])
    except Exception:
        return ""


def _detect_intent(text: str) -> dict:
    """Detect what user wants and which DataBus chains to query."""
    t = text.lower()
    chains_to_query = []
    if any(w in t for w in ["price", "worth", "value", "cost", "how much"]):
        chains_to_query.append("token_price")
    if any(w in t for w in ["safe", "scam", "rug", "honeypot", "risk", "audit", "security"]):
        chains_to_query.append("token_security")
    if any(w in t for w in ["trending", "hot", "popular", "mover", "gainer"]):
        chains_to_query.append("trending")
    if any(w in t for w in ["launch", "new token", "just launched", "fresh"]):
        chains_to_query.append("token_launches")
    if any(w in t for w in ["market", "sentiment", "fear", "greed", "global"]):
        chains_to_query.extend(["market_overview", "fear_greed"])
    if any(w in t for w in ["news", "headline", "latest", "today", "happening"]):
        chains_to_query.append("news")
    return {"chains": chains_to_query[:3]}


RMI_PERSONA = """You are the RMI Crypto AI — powered by Rug Munch Intelligence.
You run on DataBus: 93 chains, 116 providers, 3,480-doc knowledge base.
You are direct, security-first, and data-driven. Never give financial advice.
Use Telegram HTML format. Keep responses scannable. Warn about scams immediately."""


async def cmd_ai(update, ctx):
    """Full AI agent with DataBus enrichment."""
    msg = update.message
    text = " ".join(ctx.args) if ctx.args else ""

    if not text:
        chains_list = "\n".join(f"<code>{k}</code> — {v}" for k, v in list(AGENT_CHAINS.items())[:6])
        await msg.reply_text(
            f"<b>🧠 RMI Crypto AI</b>\n\n"
            f"Ask anything. I query DataBus for real-time data:\n\n{chains_list}\n\n"
            f"<b>Examples:</b>\n"
            f"<code>/ai SOL price and risk</code>\n"
            f"<code>/ai trending tokens today</code>\n"
            f"<code>/ai latest crypto scams</code>\n"
            f"<code>/ai market sentiment</code>\n\n"
            f"<i>Powered by DeepSeek V4 Flash + DataBus</i>",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    # Sanitize
    clean = text[:2000]
    for p in INJECTION_PATTERNS:
        if re.search(p, clean, re.IGNORECASE):
            await msg.reply_text("⚠️ Blocked by security filter.", parse_mode="HTML")
            return
    clean = PRIVATE_KEY_RX.sub("[REDACTED]", clean)

    await msg.chat.send_action("typing")

    # ── DataBus enrichment ──
    intent = _detect_intent(text)
    context_parts = []

    for chain in intent["chains"]:
        data = _databus_fetch(chain, limit=5)
        if data:
            context_parts.append(f"[DataBus:{chain}] {json.dumps(data)[:400]}")

    # RAG
    rag = _rag_search(text)
    if rag:
        context_parts.append(f"[RAG]\n{rag}")

    # Build prompt
    system = RMI_PERSONA
    if context_parts:
        system += "\n\nLIVE DATA:\n" + "\n\n".join(context_parts)
    system += f"\n\nTime: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}"

    try:
        body = json.dumps(
            {
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": clean},
                ],
                "max_tokens": 600,
                "temperature": 0.7,
            }
        ).encode()
        req = Request(
            DEEPSEEK_URL,
            data=body,
            headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
        )
        resp = urlopen(req, timeout=20)
        reply = json.loads(resp.read())["choices"][0]["message"]["content"]
        reply = PRIVATE_KEY_RX.sub("[REDACTED]", reply[:3800])
        await msg.reply_text(reply, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"AI failed: {e}")
        await msg.reply_text("❌ AI temporarily unavailable. Try /security, /trending, or /news.", parse_mode="HTML")
