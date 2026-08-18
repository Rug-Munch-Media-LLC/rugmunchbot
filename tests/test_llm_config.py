"""LLM fallback chain — bot-local llm_config (OpenRouter laguna/nemotron/:lowest)."""

import pytest

from rugmunchbot import llm_config


def test_snappy_config_points_at_laguna():
    cfg = llm_config.get_snappy_config()
    assert cfg["model"] == "poolside/laguna-s-2.1:free"
    assert "/chat/completions" in cfg["base_url"]


def test_long_config_points_at_nemotron():
    cfg = llm_config.get_long_config()
    assert cfg["model"] == "nvidia/nemotron-3-ultra-550b-a55b:free"


def test_cheap_fallback_is_paid_lowest():
    cfg = llm_config.get_cheap_fallback_config()
    assert cfg["model"].endswith(":lowest")


@pytest.mark.asyncio
async def test_chat_with_fallback_unknown_tier_fails_open():
    assert (
        await llm_config.chat_with_fallback([{"role": "user", "content": "hi"}], tier="nope") == ""
    )


@pytest.mark.asyncio
async def test_chat_with_fallback_missing_key_fails_open(monkeypatch):
    monkeypatch.setattr(llm_config, "OPENROUTER_BACKUP_API_KEY", "")
    assert (
        await llm_config.chat_with_fallback([{"role": "user", "content": "hi"}], tier="snappy")
        == ""
    )


@pytest.mark.asyncio
async def test_chat_with_fallback_http_error_then_paid_retry(respx_mock, monkeypatch):
    """Free tier 500 -> paid :lowest retry succeeds."""
    monkeypatch.setattr(llm_config, "OPENROUTER_BACKUP_API_KEY", "sk-test")
    snappy = llm_config.get_snappy_config()
    paid = llm_config.get_cheap_fallback_config()
    import httpx

    respx_mock.post(snappy["base_url"]).mock(return_value=httpx.Response(500))
    respx_mock.post(paid["base_url"]).mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "paid answer"}}]}
        )
    )
    answer = await llm_config.chat_with_fallback(
        [{"role": "user", "content": "hi"}], tier="snappy", max_tokens=50
    )
    assert answer == "paid answer"


@pytest.mark.asyncio
async def test_chat_with_fallback_snappy_direct_hit(respx_mock, monkeypatch):
    monkeypatch.setattr(llm_config, "OPENROUTER_BACKUP_API_KEY", "sk-test")
    snappy = llm_config.get_snappy_config()
    import httpx

    respx_mock.post(snappy["base_url"]).mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "fast answer"}}]}
        )
    )
    answer = await llm_config.chat_with_fallback([{"role": "user", "content": "hi"}], tier="snappy")
    assert answer == "fast answer"


@pytest.mark.asyncio
async def test_chat_completion_wrapper_ai_router_shape(respx_mock, monkeypatch):
    """briefing calls chat_completion(messages, model, task_type, temperature, max_tokens) -> dict."""
    monkeypatch.setattr(llm_config, "OPENROUTER_BACKUP_API_KEY", "sk-test")
    long_cfg = llm_config.get_long_config()
    import httpx

    respx_mock.post(long_cfg["base_url"]).mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "deep answer"}}]}
        )
    )
    result = await llm_config.chat_completion(
        messages=[{"role": "user", "content": "analyze"}],
        model=None,
        task_type="analysis",
        temperature=0.5,
        max_tokens=100,
    )
    assert result == {"content": "deep answer"}


@pytest.mark.asyncio
async def test_chat_completion_wrapper_fail_open(monkeypatch):
    monkeypatch.setattr(llm_config, "OPENROUTER_BACKUP_API_KEY", "")
    result = await llm_config.chat_completion([{"role": "user", "content": "x"}], task_type="chat")
    assert result == {}
