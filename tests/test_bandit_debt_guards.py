"""Regression guards for the bandit debt that blocked every push (B314 / B310).

Two classes, both demonstrably red on the pre-fix code:

* B314 — untrusted feed XML was parsed with ``xml.etree.ElementTree``, which
  expands internal entities (billion laughs). ``defusedxml`` refuses them, so
  ``parse_rss_feed`` returns ``[]`` instead of a bomb.
* B310 — the embedder POST went through ``urllib.request.urlopen``, which
  happily opens a ``file://`` URL (a local-file read driven by config). The
  httpx transport speaks http(s) only, so the same input now fails closed.
"""

from __future__ import annotations

import json

from _di import _setattr

from rugmunchbot import model_router
from rugmunchbot._vendor.news_ingest.ingest import parse_rss_feed

# Nested internal entities: 10^6 copies of "lol" if an expanding parser runs.
BILLION_LAUGHS = """<?xml version="1.0"?>
<!DOCTYPE rss [
  <!ENTITY a "lol">
  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
  <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
  <!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">
  <!ENTITY e "&d;&d;&d;&d;&d;&d;&d;&d;&d;&d;">
  <!ENTITY f "&e;&e;&e;&e;&e;&e;&e;&e;&e;&e;">
]>
<rss><channel><item><title>&f;</title></item></channel></rss>
"""


def test_parse_rss_feed_refuses_entity_expansion() -> None:
    """defusedxml rejects the DOCTYPE, so no item is ever produced."""
    assert parse_rss_feed(BILLION_LAUGHS) == []


def test_parse_rss_feed_still_reads_plain_feeds() -> None:
    """The hardening must not break ordinary RSS."""
    plain = (
        "<rss><channel><item><title>t</title><link>https://x.test/a</link></item></channel></rss>"
    )
    assert [i["title"] for i in parse_rss_feed(plain)] == ["t"]


def test_embed_refuses_file_url(tmp_path) -> None:
    """A file:// embed URL is a local-file read on urlopen; httpx fails closed."""
    local = tmp_path / "embed.json"
    local.write_text(json.dumps({"data": [{"embedding": [1.0]}]}))
    _setattr(model_router, "EMBED_URL", local.as_uri())
    assert model_router._embed(["probe"]) is None
