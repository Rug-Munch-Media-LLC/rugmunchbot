"""Config loading — env-driven defaults, no gopass at import time."""

import importlib
import os


def test_bot_token_not_bound_at_import():
    """BOT_TOKEN must not trigger gopass at module import (config loads clean)."""
    os.environ.pop("RUGMUNCH_BOT_TOKEN", None)
    os.environ.pop("TELEGRAM_RUGMUNCH_BOT_TOKEN", None)
    os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    import rugmunchbot.config as cfg

    # token resolution is on-demand (property/dynamic) — importing is safe
    assert hasattr(cfg, "BACKEND_URL")


def test_backend_url_env_override(monkeypatch):
    # Need to set env var BEFORE importing the module
    monkeypatch.setenv("RMI_BACKEND_URL", "http://test-backend:8000")
    # Force re-import to pick up the new env var
    import rugmunchbot.config as cfg

    importlib.reload(cfg)
    # PEP 562 module __getattr__ memoizes into globals() — reload does NOT
    # clear already-memoized attributes, so drop them to force re-resolution
    # through the fresh env (this is what made the test order-dependent).
    for name in ("BACKEND_URL", "BACKEND_HOST"):
        if hasattr(cfg, name):
            delattr(cfg, name)
    from rugmunchbot.config import BACKEND_URL

    assert BACKEND_URL == "http://test-backend:8000"


def test_llm_model_defaults():
    os.environ.pop("LLM_MODEL", None)
    os.environ.pop("LLM_LONG_MODEL", None)
    importlib.reload(__import__("rugmunchbot.llm_config", fromlist=["x"]))
    from rugmunchbot import llm_config
    from rugmunchbot.llm_config import get_long_config, get_snappy_config

    snappy = get_snappy_config()
    long_cfg = get_long_config()
    # Current chain (2026-10-15): hosted FREE primary lane — Groq
    # ``qwen/qwen3.8-27b`` (vision-capable, ~320 tok/s) serves BOTH chat and
    # content writing; Gemini is the free fallback; paid DeepInfra Qwen3.5-9B
    # is the FINAL rung.
    assert snappy["model"] == "qwen/qwen3.8-27b"  # hosted free primary
    assert "Qwen3.5" in llm_config.get_paid_final_config("snappy")["model"]  # paid final rung
    assert long_cfg["model"] == "qwen/qwen3.8-27b"  # ONE lane for chat + writing
