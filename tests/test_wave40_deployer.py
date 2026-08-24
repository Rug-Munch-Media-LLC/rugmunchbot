"""Wave 40 — _deployer.py contracts (was 17% covered).

Pure-logic deployer/socials resolution:

* _extract_pair_intel: creatorAddress preferred over creator, sentinel
  values ("unknown"/"0x"/"") rejected, socials merged from info.socials
  + info.websites, baseToken.info nesting accepted, junk tolerated
* never fabricates: no data → empty creator + empty socials dict
"""

from __future__ import annotations

from rugmunchbot.tools.scanner._deployer import _extract_pair_intel


def test_creator_address_preferred():
    pair = {"creatorAddress": " 0xCanonical ", "creator": "0xLegacy"}
    creator, _socials = _extract_pair_intel(pair)
    assert creator == "0xCanonical"


def test_creator_fallback_to_legacy_field():
    creator, _ = _extract_pair_intel({"creator": "0xLegacy"})
    assert creator == "0xLegacy"


def test_sentinel_creators_rejected():
    for junk in ("", "unknown", "null", "none", "0x", "0x0", None):
        creator, _ = _extract_pair_intel({"creatorAddress": junk})
        assert creator == "", f"sentinel {junk!r} leaked"


def test_socials_from_info_socials_and_websites():
    pair = {
        "info": {
            "socials": [
                {"type": "twitter", "url": "https://x.com/proj"},
                {"type": "telegram", "url": "https://t.me/proj"},
            ],
            "websites": [{"url": "https://proj.xyz"}],
        }
    }
    _, socials = _extract_pair_intel(pair)
    assert socials.get("twitter") == "https://x.com/proj"
    assert socials.get("telegram") == "https://t.me/proj"
    assert socials.get("website") == "https://proj.xyz"


def test_basetoken_info_nesting_accepted():
    pair = {"baseToken": {"info": {"socials": [{"type": "twitter", "url": "https://x.com/n"}]}}}
    _, socials = _extract_pair_intel(pair)
    assert socials.get("twitter") == "https://x.com/n"


def test_junk_tolerated_never_fabricates():
    creator, socials = _extract_pair_intel(
        {"info": {"socials": ["junk", {"no_type": True}, 42], "websites": "not-a-list"}}
    )
    assert creator == ""
    assert socials == {}


def test_empty_pair_empty_results():
    creator, socials = _extract_pair_intel({})
    assert creator == ""
    assert socials == {}
