"""Redaction covers tokens, invite links, emails and wallet secrets.

Regression classes from real journal leaks: a revoked Telegram token printed in
``InvalidToken`` (2026-10-16), invite links posted into a channel, user emails
in support hints, and wallet material pasted into a scan command.

Deliberate non-redaction: a bare 32-byte hex value is indistinguishable from a
transaction hash — blinding it would make incidents undebuggable.
"""

from __future__ import annotations

import logging

import pytest

from rugmunchbot import observability
from rugmunchbot.support.redaction import RedactionFilter, RedactionProcessor, scrub

_BOT_TOKEN = "8720275246:AAH8ft1O7RfmhX2bn4HMl2rj9eKrkFGS4EU"


@pytest.mark.parametrize(
    ("text", "marker"),
    [
        (f"token {_BOT_TOKEN} rejected", "<REDACTED>"),
        (f"https://api.telegram.org/bot{_BOT_TOKEN}/getMe", "bot<REDACTED>"),
        ("alice.wallet+1@example.co.uk pinged us", "<REDACTED_EMAIL>"),
        ("join https://t.me/+AbCdEf123456 now", "<REDACTED_INVITE>"),
        ("join t.me/joinchat/AAAAAExyz", "<REDACTED_INVITE>"),
        ("invite discord.gg/rugmunch", "<REDACTED_INVITE>"),
        ("private_key=0xdeadbeefcafebabe", "<REDACTED>"),
        ('mnemonic: "legal winner thank year wave sausage worth"', "<REDACTED>"),
        ("Authorization: Bearer sk-live-abcdef123456", "<REDACTED>"),
    ],
)
def test_secret_classes_are_scrubbed(text: str, marker: str) -> None:
    out = scrub(text)
    assert marker in out
    for leak in (_BOT_TOKEN, "example.co.uk", "AbCdEf123456", "deadbeefcafe", "sk-live"):
        assert leak not in out


def test_pem_private_key_block_is_redacted() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
    out = scrub(f"key dump:\n{pem}\nafter")
    assert "MIIEow" not in out
    assert "<REDACTED_PRIVATE_KEY>" in out


def test_transaction_hash_is_not_redacted() -> None:
    tx = "0x" + "a1" * 32
    assert scrub(f"tx {tx} confirmed") == f"tx {tx} confirmed"


def test_logging_filter_scrubs_args_and_exception_text() -> None:
    rec = logging.LogRecord(
        "t", logging.ERROR, "x", 1, "delivered to %s via %s", ("a@b.io", "t.me/+Secret1"), None
    )
    rec.exc_text = f"boom {_BOT_TOKEN}"
    RedactionFilter().filter(rec)
    assert "a@b.io" not in str(rec.args)
    assert "Secret1" not in str(rec.args)
    assert _BOT_TOKEN not in rec.exc_text


def test_structlog_processor_scrubs_nested_values() -> None:
    proc = RedactionProcessor()
    out = proc(None, "info", {"event": "x", "user": {"email": "a@b.io"}, "url": "discord.gg/abc"})
    assert out["user"]["email"] == "<REDACTED_EMAIL>"
    assert out["url"] == "<REDACTED_INVITE>"


def test_health_snapshot_never_exposes_a_token() -> None:
    """record_error feeds /healthz — the stored text must be redacted."""
    observability.record_error(kind="test", message=f"InvalidToken {_BOT_TOKEN}")
    snap = observability.health_snapshot()
    assert _BOT_TOKEN not in str(snap)
