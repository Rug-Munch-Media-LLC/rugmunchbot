"""LLM fallback chain — bot-local llm_config.

Current chain (canonical 2026-08-29): snappy = Qwen3.5-9B via DeepInfra
(cheap ~$0.08/Mtok, RAG-friendly), long = Nemotron 3.5 Lightning via
KiloCode (free), last resort = GPT-OSS 20B via OpenRouter free tier. Every
leg fails open to "" — the tests must stay hermetic (NO live LLM calls), so
both provider keys are blanked unless a test explicitly mocks HTTP.
"""

import json

import pytest

from rugmunchbot import llm_config


def test_snappy_config_points_at_deepinfra_qwen():
    # 2026-09-01 doctrine: FREE-FIRST — KiloCode StepFlash primary; the paid
    # DeepInfra Qwen3.5-9B is the FINAL rung (get_paid_final_config).
    cfg = llm_config.get_snappy_config()
    assert cfg["model"] == "stepfun/step-3.7-flash:free"
    assert cfg["base_url"].startswith(llm_config.KILOCODE_BASE)
    assert "/chat/completions" in cfg["base_url"]
    paid = llm_config.get_paid_final_config("snappy")
    assert paid["model"] == "Qwen/Qwen3.5-9B"
    assert paid["base_url"].startswith(llm_config.DEEPINFRA_BASE)
    # Fallback leg is the OpenRouter free nemotron, not another Kilo route.
    assert cfg["fallback_model"] == "nvidia/nemotron-3-super-120b-a12b:free"


def test_long_config_points_at_nemotron_lightning():
    cfg = llm_config.get_long_config()
    assert cfg["model"] == "nvidia/nemotron-3.5-lightning:free"
    assert cfg["base_url"].startswith(llm_config.KILOCODE_BASE)
    assert cfg["fallback_model"] == "nvidia/nemotron-3-ultra-550b-a55b:free"


def test_cheap_fallback_is_free_nemotron_super():
    cfg = llm_config.get_cheap_fallback_config()
    assert cfg["model"] == "nvidia/nemotron-3-super-120b-a12b:free"
    assert cfg["base_url"].startswith(llm_config.OPENROUTER_BASE)


@pytest.mark.asyncio
async def test_chat_with_fallback_unknown_tier_fails_open():
    assert (
        await llm_config.chat_with_fallback([{"role": "user", "content": "hi"}], tier="nope") == ""
    )


@pytest.mark.asyncio
async def test_chat_with_fallback_missing_key_fails_open(monkeypatch):
    # Blank EVERY leg — DeepInfra primary, KiloCode, OpenRouter, Zen — or a
    # live lane answers instead of failing open.
    monkeypatch.setattr(llm_config, "DEEPINFRA_API_KEY", "")
    monkeypatch.setattr(llm_config, "OPENROUTER_BACKUP_API_KEY", "")
    monkeypatch.setattr(llm_config, "KILOCODE_API_KEY", "")
    monkeypatch.setattr(llm_config, "ZEN_API_KEY", "")
    assert (
        await llm_config.chat_with_fallback([{"role": "user", "content": "hi"}], tier="snappy")
        == ""
    )


@pytest.mark.asyncio
async def test_chat_with_fallback_http_error_then_fallback_retry(respx_mock, monkeypatch):
    """Primary (DeepInfra) 500 -> KiloCode StepFlash fallback succeeds."""
    monkeypatch.setattr(llm_config, "DEEPINFRA_API_KEY", "sk-test-deepinfra")
    monkeypatch.setattr(llm_config, "KILOCODE_API_KEY", "sk-test-kilocode")
    monkeypatch.setattr(llm_config, "OPENROUTER_BACKUP_API_KEY", "sk-test")
    snappy = llm_config.get_snappy_config()
    import httpx

    respx_mock.post(snappy["base_url"]).mock(return_value=httpx.Response(500))
    # The fallback leg is now KiloCode StepFlash.
    respx_mock.post(snappy["fallback_base_url"]).mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "fallback answer"}}]}
        )
    )
    answer = await llm_config.chat_with_fallback(
        [{"role": "user", "content": "hi"}], tier="snappy", max_tokens=50
    )
    assert answer == "fallback answer"


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
    # Blank EVERY leg (DeepInfra, Kilo, OpenRouter, Zen) or a live lane
    # answers instead of failing open.
    monkeypatch.setattr(llm_config, "DEEPINFRA_API_KEY", "")
    monkeypatch.setattr(llm_config, "OPENROUTER_BACKUP_API_KEY", "")
    monkeypatch.setattr(llm_config, "KILOCODE_API_KEY", "")
    monkeypatch.setattr(llm_config, "ZEN_API_KEY", "")
    result = await llm_config.chat_completion([{"role": "user", "content": "x"}], task_type="chat")
    assert result == {}


@pytest.mark.asyncio
async def test_kilo_nemotron_rung_serves_before_zen(respx_mock, monkeypatch):
    """2026-08-25 chain: Kilo primary + OR fallback fail → the KiloCode
    sibling Nemotron (ultra for long tier) serves BEFORE Zen."""
    monkeypatch.setattr(llm_config, "OPENROUTER_BACKUP_API_KEY", "sk-or")
    monkeypatch.setattr(llm_config, "KILOCODE_API_KEY", "sk-kilo")
    monkeypatch.setattr(llm_config, "ZEN_API_KEY", "sk-zen")
    import httpx

    long_cfg = llm_config.get_long_config()
    kilo_base = long_cfg["base_url"]
    or_base = f"{llm_config.OPENROUTER_BASE}/chat/completions"
    llm_config.get_kilo_nemotron_fallback_config("long")
    zen = llm_config.get_zen_fallback_config("long")

    # NOTE: kilo primary and kilo-nemotron rung share the KiloCode base URL.
    # Route by model: primary Lightning 500s, sibling Ultra serves.
    def _kilo_router(request):
        body = json.loads(request.content)
        if body["model"] == long_cfg["model"]:
            return httpx.Response(500)
        return httpx.Response(200, json={"choices": [{"message": {"content": "kilo ultra"}}]})

    respx_mock.post(kilo_base).mock(side_effect=_kilo_router)
    respx_mock.post(or_base).mock(return_value=httpx.Response(429))
    zen_route = respx_mock.post(zen["base_url"]).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "zen"}}]})
    )

    answer = await llm_config.chat_with_fallback(
        [{"role": "user", "content": "hi"}], tier="long", max_tokens=50
    )
    assert answer == "kilo ultra"
    assert zen_route.call_count == 0  # zen must NOT be reached


@pytest.mark.asyncio
async def test_zen_last_resort_serves_when_all_else_fails(respx_mock, monkeypatch):
    """Full outage of Kilo + OpenRouter → Zen ultra keeps chat alive."""
    monkeypatch.setattr(llm_config, "OPENROUTER_BACKUP_API_KEY", "sk-or")
    monkeypatch.setattr(llm_config, "KILOCODE_API_KEY", "sk-kilo")
    monkeypatch.setattr(llm_config, "ZEN_API_KEY", "sk-zen")
    import httpx

    long_cfg = llm_config.get_long_config()
    kilo_base = long_cfg["base_url"]
    or_base = f"{llm_config.OPENROUTER_BASE}/chat/completions"
    zen = llm_config.get_zen_fallback_config("long")

    # Kilo primary + sibling rung share the relay base URL; fail them all.
    respx_mock.post(kilo_base).mock(return_value=httpx.Response(500))
    respx_mock.post(or_base).mock(return_value=httpx.Response(429))
    respx_mock.post(zen["base_url"]).mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "zen survived"}}]}
        )
    )
    answer = await llm_config.chat_with_fallback(
        [{"role": "user", "content": "hi"}], tier="long", max_tokens=50
    )
    assert answer == "zen survived"


def test_fallback_tier_model_selection():
    """lightning = short/snappy, ultra-550b = long/reasoned — both lanes."""
    assert "lightning" in llm_config.get_kilo_nemotron_fallback_config("snappy")["model"]
    assert "ultra" in llm_config.get_kilo_nemotron_fallback_config("long")["model"]
    # 2026-09-01: zen snappy = laguna-s-2.1-free (the nemotron lane hung)
    assert "laguna" in llm_config.get_zen_fallback_config("snappy")["model"]
    assert "ultra" in llm_config.get_zen_fallback_config("long")["model"]
