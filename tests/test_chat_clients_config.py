"""Config/owner helpers in chat_ai/_llm/_clients.py (pure, env-driven)."""

from rugmunchbot.handlers.chat_ai._llm import _clients


def test_owner_mode_true_false_and_fail_closed(monkeypatch):
    monkeypatch.setattr("rugmunchbot.states.is_owner", lambda uid: uid == 42)
    assert _clients._owner_mode(42) is True
    assert _clients._owner_mode(7) is False

    def _boom(uid):
        raise RuntimeError("states broken")

    monkeypatch.setattr("rugmunchbot.states.is_owner", _boom)
    assert _clients._owner_mode(42) is False  # fail-closed on any error


def test_owner_augment_messages_copy_on_write(monkeypatch):
    monkeypatch.setattr("rugmunchbot.states.is_owner", lambda uid: uid == 42)
    msgs = [{"role": "system", "content": "base"}, {"role": "user", "content": "hi"}]
    out = _clients._owner_augment_messages(msgs, 42)
    assert out is not msgs
    assert msgs[0]["content"] == "base"  # original untouched
    assert out[0]["content"].startswith("base")
    assert "OWNER MODE" in out[0]["content"]
    assert out[1] == msgs[1]

    same = _clients._owner_augment_messages(msgs, 7)
    assert same is msgs  # non-owner → same object, zero copies


def test_local_chat_first_enabled_env_gate(monkeypatch):
    monkeypatch.setattr("rugmunchbot.llm_config.LOCAL_LLM_ENABLED", True)
    monkeypatch.setenv("RMI_LOCAL_CHAT_FIRST", "1")
    assert _clients._local_chat_first_enabled() is True

    monkeypatch.setenv("RMI_LOCAL_CHAT_FIRST", "0")
    assert _clients._local_chat_first_enabled() is False

    monkeypatch.delenv("RMI_LOCAL_CHAT_FIRST", raising=False)
    monkeypatch.setattr("rugmunchbot.llm_config.LOCAL_LLM_ENABLED", False)
    assert _clients._local_chat_first_enabled() is False


def test_local_snappy_patience_env_override(monkeypatch):
    monkeypatch.setenv("RMI_LOCAL_SNAPPY_PATIENCE", "3.5")
    assert _clients._local_snappy_patience() == 3.5

    monkeypatch.setenv("RMI_LOCAL_SNAPPY_PATIENCE", "not-a-float")
    assert _clients._local_snappy_patience() == 12.0

    monkeypatch.delenv("RMI_LOCAL_SNAPPY_PATIENCE", raising=False)
    assert _clients._local_snappy_patience() == 12.0
