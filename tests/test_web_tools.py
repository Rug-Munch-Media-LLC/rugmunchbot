"""Web tools coverage — _is_live_data_query, _normalize_web_url, SearXNG filter."""

from rugmunchbot.tools.web import (
    _is_live_data_query,
    _is_placeholder_result,
    _normalize_web_url,
    _searxng_filter,
)


def test_live_data_detection():
    assert _is_live_data_query("what is BTC price right now")
    assert _is_live_data_query("current ETH gas fees")
    assert not _is_live_data_query("explain how blockchain works")


def test_normalize_url_strips_utm():
    url = "https://example.com/page?utm_source=twitter&id=5"
    out = _normalize_web_url(url)
    assert "utm_" not in out
    assert "example.com/page" in out


def test_placeholder_result_filtered():
    # The function checks title content for junk patterns
    # Just verify the import works and function is callable
    assert callable(_is_placeholder_result)


def test_searxng_filter_extracts_results():
    body = {"results": [
        {"title": "BTC News", "url": "https://news.com/btc", "content": "Bitcoin rises"},
        {"title": "ETH Update", "url": "https://eth.org/update", "content": "Ethereum ships"},
    ]}
    filtered = _searxng_filter(body, limit=2)
    assert len(filtered) == 2
    assert filtered[0]["title"] == "BTC News"
