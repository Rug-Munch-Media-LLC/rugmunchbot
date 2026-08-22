"""Test wave 22b — Ghost Admin API client pure helpers (JWT, lexical).

Contracts read from source (2026-08-22). Pure logic only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from rugmunchbot.channels.personal_channel import _lexical_body, ghost_admin_jwt


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def test_ghost_admin_jwt_rejects_malformed_keys() -> None:
    for bad in ("", "no-colon", ":secret", "id:", "a:b:c"):
        with pytest.raises(ValueError, match="<id>:<secret>"):
            ghost_admin_jwt(bad)


def test_ghost_admin_jwt_signs_real_hs256_token() -> None:
    token = ghost_admin_jwt("myid:mysecret")
    header_b64, payload_b64, sig_b64 = token.split(".")
    header = json.loads(_b64url_decode(header_b64))
    payload = json.loads(_b64url_decode(payload_b64))
    assert header == {"alg": "HS256", "typ": "JWT", "kid": "myid"}
    assert payload["aud"] == "/admin/"
    assert payload["exp"] - payload["iat"] == 300  # 5-minute window
    # Signature verifies against the secret over the signing input.
    expected = hmac.new(
        b"mysecret", f"{header_b64}.{payload_b64}".encode("ascii"), hashlib.sha256
    ).digest()
    assert _b64url_decode(sig_b64) == expected


def test_lexical_body_paragraph_structure() -> None:
    out = json.loads(_lexical_body("First para.\n\nSecond para."))
    root = out["root"]
    assert root["type"] == "root"
    assert len(root["children"]) == 2
    first_text = root["children"][0]["children"][0]["text"]
    second_text = root["children"][1]["children"][0]["text"]
    assert first_text == "First para."
    assert second_text == "Second para."


def test_lexical_body_folds_newlines_and_handles_empty() -> None:
    folded = json.loads(_lexical_body("line one\nline two"))
    text = folded["root"]["children"][0]["children"][0]["text"]
    assert text == "line one line two"  # single paragraph, newlines folded
    empty = json.loads(_lexical_body(""))
    assert empty["root"]["children"] == []
