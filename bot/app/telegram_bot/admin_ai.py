#!/usr/bin/env python3
"""
RMI AI Gateway — Access AI APIs through Telegram
==================================================
Query DeepSeek, local llama.cpp, OpenRouter from the admin bot.
Streaming responses, model switching, usage tracking.
"""

import os
from datetime import UTC, datetime

import httpx

# ══════════════════════════════════════════════
# AVAILABLE MODELS
# ══════════════════════════════════════════════

# Check what's actually available from env vars
MODELS = {}

# DeepSeek (paid Flash API)
if os.getenv("DEEPSEEK_API_KEY"):
    MODELS["deepseek-chat"] = {
        "name": "DeepSeek V3",
        "provider": "deepseek",
        "api_url": "https://api.deepseek.com/v1/chat/completions",
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
        "cost_per_1k": "$0.001",
        "context": "64K",
    }

# OpenRouter (fallback to OPENROUTER_API_KEY or DEEPSEEK_API_KEY for base URL)
if os.getenv("OPENROUTER_API_KEY"):
    MODELS["openrouter/deepseek/deepseek-chat"] = {
        "name": "DeepSeek V3 (OpenRouter)",
        "provider": "openrouter",
        "api_url": "https://openrouter.ai/api/v1/chat/completions",
        "api_key": os.getenv("OPENROUTER_API_KEY"),
        "cost_per_1k": "$0.001",
        "context": "64K",
    }

# Local llama.cpp (Talos, systemd)
MODELS["llama:qwen-chat"] = {
    "name": "Qwen3.5 4B Chat (Local)",
    "provider": "llama",
    "api_url": "http://172.22.0.1:8085/v1/chat/completions",
    "api_key": "",
    "cost_per_1k": "FREE",
    "context": "8K",
}

MODELS["llama:qwen-analysis"] = {
    "name": "Qwen3.5 9B Analysis (Local)",
    "provider": "llama",
    "api_url": "http://172.22.0.1:8086/v1/chat/completions",
    "api_key": "",
    "cost_per_1k": "FREE",
    "context": "8K",
}

# Default model
DEFAULT_MODEL = (
    "deepseek-chat" if "deepseek-chat" in MODELS else next(iter(MODELS.keys())) if MODELS else None
)

# ══════════════════════════════════════════════
# PER-USER STATE
# ══════════════════════════════════════════════


class AISession:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.model = DEFAULT_MODEL
        self.history: list[dict] = []  # [{role, content}]
        self.total_tokens = 0
        self.total_cost = 0.0
        self.last_used = datetime.now(UTC)


_sessions: dict[int, AISession] = {}


def get_session(user_id: int) -> AISession:
    if user_id not in _sessions:
        _sessions[user_id] = AISession(user_id)
    return _sessions[user_id]


def set_model(user_id: int, model_id: str) -> bool:
    if model_id in MODELS:
        get_session(user_id).model = model_id
        return True
    return False


def get_model(user_id: int) -> str:
    s = get_session(user_id)
    return s.model or DEFAULT_MODEL or next(iter(MODELS.keys()))


# ══════════════════════════════════════════════
# CHAT COMPLETION
# ══════════════════════════════════════════════


async def chat(user_id: int, prompt: str, system_prompt: str = "") -> dict:
    """Send a chat completion request. Returns {ok, content, model, tokens, error}."""
    session = get_session(user_id)
    model_id = session.model
    if not model_id or model_id not in MODELS:
        return {
            "ok": False,
            "content": "",
            "model": "",
            "tokens": 0,
            "error": "No model configured. Set DEEPSEEK_API_KEY or OPENROUTER_API_KEY env var.",
        }

    model = MODELS[model_id]

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    # Include last few exchanges for context (limit to 4K tokens worth)
    context = session.history[-6:] if session.history else []
    messages.extend(context)
    messages.append({"role": "user", "content": prompt})

    headers = {"Content-Type": "application/json"}
    if model["api_key"]:
        headers["Authorization"] = f"Bearer {model['api_key']}"

    body = {
        "model": model_id.replace("llama:", "").replace("openrouter/", ""),
        "messages": messages,
        "max_tokens": 2000,
        "temperature": 0.7,
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(model["api_url"], json=body, headers=headers)
            if r.status_code != 200:
                return {
                    "ok": False,
                    "content": "",
                    "model": model_id,
                    "tokens": 0,
                    "error": f"API error {r.status_code}: {r.text[:300]}",
                }

            data = r.json()
            content = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)

            # Update session
            session.history.append({"role": "user", "content": prompt})
            session.history.append({"role": "assistant", "content": content})
            session.total_tokens += tokens
            session.last_used = datetime.now(UTC)

            # Keep history manageable
            if len(session.history) > 20:
                session.history = session.history[-20:]

            return {
                "ok": True,
                "content": content,
                "model": model["name"],
                "tokens": tokens,
                "error": "",
            }

    except httpx.ConnectError:
        return {
            "ok": False,
            "content": "",
            "model": model_id,
            "tokens": 0,
            "error": f"Cannot connect to {model['provider']} API. Is the service running?",
        }
    except Exception as e:
        return {
            "ok": False,
            "content": "",
            "model": model_id,
            "tokens": 0,
            "error": str(e)[:300],
        }


def clear_history(user_id: int):
    s = get_session(user_id)
    s.history = []


def get_usage(user_id: int) -> dict:
    s = get_session(user_id)
    return {
        "tokens": s.total_tokens,
        "cost": s.total_cost,
        "model": s.model,
        "messages": len(s.history),
    }


# ══════════════════════════════════════════════
# MODEL LIST & HELPERS
# ══════════════════════════════════════════════


def list_models() -> str:
    """Format available models for display."""
    lines = ["🤖 *Available AI Models*", ""]
    for mid, m in MODELS.items():
        emoji = "🏠" if "llama" in mid else "☁️" if "deepseek" in mid else "🌐"
        lines.append(f"{emoji} *{m['name']}* — {m['cost_per_1k']}/1K tokens, {m['context']} ctx")
        lines.append(f"  `{mid}`")
    lines.append("")
    lines.append("Switch: `/ai model <id>`")
    return "\n".join(lines)


def model_keyboard():
    """Keyboard for model selection."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    buttons = []
    for mid, m in MODELS.items():
        emoji = "🏠" if "llama" in mid else "☁️"
        label = f"{emoji} {m['name']}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"ai_model_{mid}")])
    buttons.append([InlineKeyboardButton("🧹 Clear History", callback_data="ai_clear")])
    buttons.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="main")])
    return InlineKeyboardMarkup(buttons)
