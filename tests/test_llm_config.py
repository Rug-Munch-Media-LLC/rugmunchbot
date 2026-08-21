"""LLM fallback chain — bot-local llm_config.

Current chain (canonical 2026-08-19): snappy = StepFlash 3.7 via KiloCode
(free), long = Nemotron 3.5 Lightning via KiloCode (free), last resort =
GPT-OSS 20B via OpenRouter free tier. Every leg fails open to "" — the
tests must stay hermetic (NO live LLM calls), so both provider keys are
blanked unless a test explicitly mocks HTTP.
"""

import pytest

from rugmunchbot import llm_config


def test_snappy_config_points_at_stepflash():
    cfg = llm_config.get_snappy_config()
    assert cfg["model"] == "stepfun/step-3.7-flash:free"
    assert cfg["base_url"].startswith(llm_config.KILOCODE_BASE)
    assert "/chat/completions" in cfg["base_url"]
    # Fallback leg is the OpenRouter free model, not another KiloCode route.
    assert cfg["fallback_model"] == "openai/gpt-oss-20b:free"


def test_long_config_points_at_nemotron_lightning():
    cfg = llm_config.get_long_config()
    assert cfg["model"] == "nvidia/nemotron-3.5-lightning:free"
    assert cfg["base_url"].startswith(llm_config.KILOCODE_BASE)
    assert cfg["fallback_model"] == "nvidia/nemotron-3-ultra-550b-a55b:free"


def test_cheap_fallback_is_free_gpt_oss():
    cfg = llm_config.get_cheap_fallback_config()
    assert cfg["model"] == "openai/gpt-oss-20b:free"
    assert cfg["base_url"].startswith(llm_config.OPENROUTER_BASE)


@pytest.mark.asyncio
async def test_chat_with_fallback_unknown_tier_fails_open():
    assert (
        await llm_config.chat_with_fallback([{"role": "user", "content": "hi"}], tier="nope") == ""
    )


@pytest.mark.asyncio
async def test_chat_with_fallback_missing_key_fails_open(monkeypatch):
    # BOTH provider keys must be blank — the chain is KiloCode primary +
    # OpenRouter fallback; blanking only one leg would let the other make a
    # live call (and return a real answer instead of failing open).
    monkeypatch.setattr(llm_config, "OPENROUTER_BACKUP_API_KEY", "")
    monkeypatch.setattr(llm_config, "KILOCODE_API_KEY", "")
    assert (
        await llm_config.chat_with_fallback([{"role": "user", "content": "hi"}], tier="snappy")
        == ""
    )


@pytest.mark.asyncio
async def test_chat_with_fallback_http_error_then_fallback_retry(respx_mock, monkeypatch):
    """Primary (KiloCode) 500 -> OpenRouter free fallback succeeds."""
    monkeypatch.setattr(llm_config, "OPENROUTER_BACKUP_API_KEY", "sk-test")
    monkeypatch.setattr(llm_config, "KILOCODE_API_KEY", "sk-test-kilocode")
    snappy = llm_config.get_snappy_config()
    paid = llm_config.get_cheap_fallback_config()
    import httpx

    respx_mock.post(snappy["base_url"]).mock(return_value=httpx.Response(500))
    # The fallback leg AND the cheap last resort share the OpenRouter
    # chat/completions URL — one mock serves both.
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
    monkeypatch.setattr(llm_config, "KILOCODE_API_KEY", "sk-test-kilocode")
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
    monkeypatch.setattr(llm_config, "KILOCODE_API_KEY", "sk-test-kilocode")
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
    # Blank BOTH provider keys (see test_chat_with_fallback_missing_key_fails_open).
    monkeypatch.setattr(llm_config, "OPENROUTER_BACKUP_API_KEY", "")
    monkeypatch.setattr(llm_config, "KILOCODE_API_KEY", "")
    result = await llm_config.chat_completion([{"role": "user", "content": "x"}], task_type="chat")
    assert result == {}
