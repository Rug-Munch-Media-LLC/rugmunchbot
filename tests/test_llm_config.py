"""LLM fallback chain — bot-local llm_config.

Current chain (canonical 2026-09-18, after the OpenCode Zen lane was retired):
snappy = ``poolside/laguna-s-2.1:free`` via KiloCode, long =
``nvidia/nemotron-3-ultra-550b-a55b:free`` via KiloCode, then OpenRouter
Nemotron Super, then the KiloCode sibling model, then the KiloCode gateway
(``kilo-auto/free``), and finally the paid DeepInfra Qwen3.5-9B backstop.
Every leg fails open to "" — the tests must stay hermetic (NO live LLM calls),
so every provider key is blanked unless a test explicitly mocks HTTP.
"""

import json

import httpx
import pytest

from rugmunchbot import llm_config


@pytest.fixture(autouse=True)
def _hermetic_llm_lanes(respx_mock, monkeypatch):
    """Keep every test off live LLM endpoints.

    ``chat_with_fallback`` fronts the tier chain with the capability router
    (a local-embedding classifier) and, for snappy/local-first tasks, a live
    self-hosted Qwythos-9B on :8009 — both reach real servers on Talos, so an
    unmocked call would answer instead of exercising the tier chain. These tests
    assert the tier chain, so the router is disabled and the local lane is
    mocked to a refused connection (which fails open to "").
    """
    monkeypatch.setattr(llm_config, "RMI_CAPABILITY_ROUTER", False)
    respx_mock.post(llm_config.LOCAL_LLM_URL).mock(
        side_effect=httpx.ConnectError("local lane disabled in tests")
    )


def test_snappy_config_points_at_kilocode_laguna():
    # 2026-09-18 doctrine: FREE-FIRST — KiloCode laguna-s-2.1 primary (replaced
    # stepfun/step-3.7-flash, which returned empty content below ~2000
    # max_tokens); the paid DeepInfra Qwen3.5-9B is the FINAL rung
    # (get_paid_final_config).
    cfg = llm_config.get_snappy_config()
    assert cfg["model"] == "poolside/laguna-s-2.1:free"
    assert cfg["base_url"].startswith(llm_config.KILOCODE_BASE)
    assert "/chat/completions" in cfg["base_url"]
    paid = llm_config.get_paid_final_config("snappy")
    assert paid["model"] == "Qwen/Qwen3.5-9B"
    assert paid["base_url"].startswith(llm_config.DEEPINFRA_BASE)
    # Fallback leg is the OpenRouter free nemotron, not another Kilo route.
    assert cfg["fallback_model"] == "nvidia/nemotron-3-super-120b-a12b:free"


def test_long_config_points_at_nemotron_ultra():
    # 2026-09-18: nemotron-3.5-lightning was dropped (leaked "thinking process");
    # the deep tier is now nemotron-3-ultra-550b via KiloCode.
    cfg = llm_config.get_long_config()
    assert cfg["model"] == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert cfg["base_url"].startswith(llm_config.KILOCODE_BASE)
    assert cfg["fallback_model"] == "nvidia/nemotron-3-super-120b-a12b:free"


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
    # Blank EVERY leg — local, DeepInfra, KiloCode, OpenRouter — or a live lane
    # answers instead of failing open. (OpenCode Zen was removed 2026-09-18.)
    monkeypatch.setattr(llm_config, "DEEPINFRA_API_KEY", "")
    monkeypatch.setattr(llm_config, "OPENROUTER_BACKUP_API_KEY", "")
    monkeypatch.setattr(llm_config, "KILOCODE_API_KEY", "")
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
    # Blank EVERY leg (local, DeepInfra, Kilo, OpenRouter) or a live lane
    # answers instead of failing open. (OpenCode Zen was removed 2026-09-18.)
    monkeypatch.setattr(llm_config, "DEEPINFRA_API_KEY", "")
    monkeypatch.setattr(llm_config, "OPENROUTER_BACKUP_API_KEY", "")
    monkeypatch.setattr(llm_config, "KILOCODE_API_KEY", "")
    result = await llm_config.chat_completion([{"role": "user", "content": "x"}], task_type="chat")
    assert result == {}


@pytest.mark.asyncio
async def test_kilo_nemotron_rung_serves_before_kilo_auto(respx_mock, monkeypatch):
    """Kilo primary + OpenRouter fallback fail → the KiloCode sibling model
    (laguna for long) serves BEFORE the kilo-auto/free gateway rung."""
    monkeypatch.setattr(llm_config, "OPENROUTER_BACKUP_API_KEY", "sk-or")
    monkeypatch.setattr(llm_config, "KILOCODE_API_KEY", "sk-kilo")
    import httpx

    long_cfg = llm_config.get_long_config()
    kilo_base = long_cfg["base_url"]
    or_base = f"{llm_config.OPENROUTER_BASE}/chat/completions"
    sibling = llm_config.get_kilo_nemotron_fallback_config("long")
    gateway = llm_config.get_kilo_final_fallback_config("long")

    # kilo primary, sibling and final gateway all share the KiloCode base URL —
    # route by model and record what was actually requested. Primary fails,
    # sibling (laguna) serves.
    seen_models: list[str] = []

    def _kilo_router(request):
        body = json.loads(request.content)
        seen_models.append(body["model"])
        if body["model"] == long_cfg["model"]:
            return httpx.Response(500)
        return httpx.Response(200, json={"choices": [{"message": {"content": "kilo sibling"}}]})

    respx_mock.post(kilo_base).mock(side_effect=_kilo_router)
    respx_mock.post(or_base).mock(return_value=httpx.Response(429))

    answer = await llm_config.chat_with_fallback(
        [{"role": "user", "content": "hi"}], tier="long", max_tokens=50
    )
    assert answer == "kilo sibling"
    # The sibling served, so the kilo-auto gateway rung must NOT have been tried.
    assert seen_models == [long_cfg["model"], sibling["model"]]
    assert gateway["model"] not in seen_models


@pytest.mark.asyncio
async def test_kilo_auto_last_resort_serves_when_all_else_fails(respx_mock, monkeypatch):
    """Full outage of the Kilo primary + sibling and OpenRouter → the KiloCode
    ``kilo-auto/free`` gateway rung keeps chat alive (replaces retired Zen)."""
    monkeypatch.setattr(llm_config, "OPENROUTER_BACKUP_API_KEY", "sk-or")
    monkeypatch.setattr(llm_config, "KILOCODE_API_KEY", "sk-kilo")
    import httpx

    long_cfg = llm_config.get_long_config()
    kilo_base = long_cfg["base_url"]
    or_base = f"{llm_config.OPENROUTER_BASE}/chat/completions"
    gateway = llm_config.get_kilo_final_fallback_config("long")

    # Kilo primary + sibling + gateway share the relay base URL; only the
    # gateway model (kilo-auto/free) answers.
    def _kilo_router(request):
        body = json.loads(request.content)
        if body["model"] == gateway["model"]:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "kilo-auto survived"}}]}
            )
        return httpx.Response(500)

    respx_mock.post(kilo_base).mock(side_effect=_kilo_router)
    respx_mock.post(or_base).mock(return_value=httpx.Response(429))
    answer = await llm_config.chat_with_fallback(
        [{"role": "user", "content": "hi"}], tier="long", max_tokens=50
    )
    assert answer == "kilo-auto survived"


def test_fallback_tier_model_selection():
    """snappy sibling = ultra-550b, long sibling = laguna; the final free rung
    is the kilo-auto gateway for both tiers (Zen retired 2026-09-18)."""
    assert "ultra" in llm_config.get_kilo_nemotron_fallback_config("snappy")["model"]
    assert "laguna" in llm_config.get_kilo_nemotron_fallback_config("long")["model"]
    assert llm_config.get_kilo_final_fallback_config("snappy")["model"] == "kilo-auto/free"
    assert llm_config.get_kilo_final_fallback_config("long")["model"] == "kilo-auto/free"
