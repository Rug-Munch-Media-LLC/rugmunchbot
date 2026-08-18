"""Settings shim — env-driven with backend-matching defaults."""

from rugmunchbot._vendor.settings_shim import resolve_service_url


def test_defaults_match_backend(monkeypatch):
    # Clear env to test defaults
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.delenv("INFINITY_URL", raising=False)
    monkeypatch.delenv("EMBEDDER_PRIMARY", raising=False)
    monkeypatch.delenv("EMBEDDER_PRIMARY_MODEL", raising=False)
    monkeypatch.delenv("EMBEDDER_CACHE_TTL", raising=False)
    monkeypatch.delenv("EMBEDDER_CACHE_MAXSIZE", raising=False)

    import importlib

    shim = importlib.reload(__import__("rugmunchbot._vendor.settings_shim", fromlist=["x"]))

    assert shim.settings.redis_url == "redis://localhost:6379/0"
    assert shim.settings.qdrant_url == "http://localhost:6333"
    assert shim.settings.infinity_url == "http://localhost:8001"
    assert shim.settings.embedder_primary == "infinity"
    assert shim.settings.embedder_primary_model == "BAAI/bge-m3"
    assert shim.settings.embedder_cache_ttl == 86400
    assert shim.settings.embedder_cache_maxsize == 10000


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "http://qdrant-test:6333")
    monkeypatch.setenv("EMBEDDER_PRIMARY", "openrouter")
    import importlib

    shim = importlib.reload(__import__("rugmunchbot._vendor.settings_shim", fromlist=["x"]))
    assert shim.settings.qdrant_url == "http://qdrant-test:6333"
    assert shim.settings.embedder_primary == "openrouter"


def test_mutable_attribute_for_crawler_fallback(monkeypatch):
    """rag crawler assigns settings.infinity_url — must be writable."""
    import importlib

    shim = importlib.reload(__import__("rugmunchbot._vendor.settings_shim", fromlist=["x"]))
    shim.settings.infinity_url = "http://fallback:8001"
    assert shim.settings.infinity_url == "http://fallback:8001"


def test_resolve_service_url_local(monkeypatch):
    monkeypatch.delenv("QDRANT_URL", raising=False)
    assert (
        resolve_service_url(
            "QDRANT_URL", docker="http://rmi-qdrant:6333", local="http://localhost:6333"
        )
        == "http://localhost:6333"
    )


def test_resolve_service_url_env(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "http://env-qdrant:6333")
    assert (
        resolve_service_url("QDRANT_URL", docker="http://x", local="http://y")
        == "http://env-qdrant:6333"
    )
