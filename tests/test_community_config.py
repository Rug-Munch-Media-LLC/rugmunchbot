"""Community config + store: safe defaults, opt-in keys, per-chat isolation."""

from __future__ import annotations

import json

import pytest

import rugmunchbot.config as botcfg
from rugmunchbot.moderation import chatconf, config, store
from rugmunchbot.moderation.harness_fakes import CHAT_ID

OTHER_CHAT = -1009999999999
OWNER_ID = 424242


@pytest.fixture
def cfg_path(_rig, tmp_path):
    return tmp_path / "moderation.json"


@pytest.fixture(autouse=True)
def _rig(tmp_path, monkeypatch):
    monkeypatch.setattr(botcfg, "OWNER_IDS", {OWNER_ID}, raising=False)
    store.use_backend(store.MemoryStore())
    store.reset_memory()
    path = tmp_path / "moderation.json"
    path.write_text(json.dumps({"chats": {str(CHAT_ID): {}}}))
    original = config.CONFIG_PATH
    config.use_config_path(path)
    yield
    config.use_config_path(original)
    store.use_backend(None)
    store.reset_memory()


def test_defaults_are_all_off():
    cfg = chatconf.load_community_config(CHAT_ID)
    assert cfg.welcome_enabled is False
    assert cfg.goodbye_enabled is False
    assert cfg.filters_enabled is False
    assert cfg.enforce_gban is False
    assert cfg.scheduled_enabled is False
    assert cfg.notes_admins_only is True
    assert cfg.welcome_text == ""
    assert cfg.any_enabled() is False


def test_block_enables_only_requested_feature(cfg_path):
    data = {
        "chats": {
            str(CHAT_ID): {"community": {"welcome_enabled": True, "welcome_text": "Hi {name}"}}
        }
    }
    cfg_path.write_text(json.dumps(data))
    cfg = chatconf.load_community_config(CHAT_ID)
    assert cfg.welcome_enabled is True
    assert cfg.welcome_text == "Hi {name}"
    assert cfg.filters_enabled is False
    assert cfg.enforce_gban is False


def test_unknown_keys_dropped_and_non_bool_is_false(cfg_path):
    data = {
        "chats": {
            str(CHAT_ID): {
                "community": {"welcome_enabled": "yes", "filters_enabled": 1, "auto_ban": True}
            }
        }
    }
    cfg_path.write_text(json.dumps(data))
    cfg = chatconf.load_community_config(CHAT_ID)
    assert cfg.welcome_enabled is False  # only literal True counts
    assert cfg.filters_enabled is False
    assert not hasattr(cfg, "auto_ban")


def test_int_clamps(cfg_path):
    data = {
        "chats": {str(CHAT_ID): {"community": {"filter_replies_per_min": 999, "schedule_max": 0}}}
    }
    cfg_path.write_text(json.dumps(data))
    cfg = chatconf.load_community_config(CHAT_ID)
    assert cfg.filter_replies_per_min == 10
    assert cfg.schedule_max == 1


def test_text_is_truncated(cfg_path):
    data = {"chats": {str(CHAT_ID): {"community": {"welcome_text": "x" * 900}}}}
    cfg_path.write_text(json.dumps(data))
    assert len(chatconf.load_community_config(CHAT_ID).welcome_text) == chatconf.MAX_TEXT


async def test_store_override_wins_over_json(cfg_path):
    data = {
        "chats": {str(CHAT_ID): {"community": {"welcome_enabled": True, "welcome_text": "json"}}}
    }
    cfg_path.write_text(json.dumps(data))
    await store.set_field(store.chat_key(CHAT_ID, "settings"), "welcome_text", "admin override")
    cfg = await chatconf.effective(CHAT_ID)
    assert cfg.welcome_text == "admin override"
    assert cfg.welcome_enabled is True


async def test_store_is_per_chat():
    await store.set_field(store.chat_key(CHAT_ID, "notes"), "rules", "be nice")
    assert await store.get_field(store.chat_key(OTHER_CHAT, "notes"), "rules") is None
    assert await store.get_field(store.chat_key(CHAT_ID, "notes"), "rules") == "be nice"


async def test_field_cap_refuses_new_keys():
    key = store.chat_key(CHAT_ID, "notes")
    for i in range(3):
        assert await store.set_field(key, f"n{i}", "x", max_fields=3) is True
    assert await store.set_field(key, "n3", "x", max_fields=3) is False
    assert await store.set_field(key, "n0", "updated", max_fields=3) is True


def test_redis_persistence_is_opt_in(monkeypatch):
    monkeypatch.delenv("RUGMUNCH_MODERATION_REDIS", raising=False)
    assert store.redis_enabled() is False
    monkeypatch.setenv("RUGMUNCH_MODERATION_REDIS", "1")
    assert store.redis_enabled() is True
