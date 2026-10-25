"""Hashed-at-rest API token helpers for the rugmunchbot DB layer.

Tokens are stored as sha256 hex digests; the plaintext is never persisted.
``token_prefix`` keeps the first 12 characters for list/UX display only and is
never used for authentication.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import sqlite3
from typing import Any

from ._base import get_db

# ── API tokens (hashed at rest) ──

API_TOKEN_PREFIX_LEN = 12


def hash_api_token(raw_token: str) -> str:
    """Return the hex sha256 digest of a raw API token — the value stored at rest."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def token_prefix(raw_token: str, length: int = API_TOKEN_PREFIX_LEN) -> str:
    """Non-sensitive display prefix (first ``length`` chars) for /api list.

    Never used for authentication — only to keep the list UX working without
    exposing the raw secret.
    """
    return raw_token[:length]


def _is_hashed_token(stored: str) -> bool:
    """True when ``stored`` is a 64-char lowercase hex sha256 digest (not plaintext)."""
    return len(stored) == 64 and all(c in "0123456789abcdef" for c in stored)


def verify_api_token(raw_token: str) -> dict[str, Any] | None:
    """Verify a presented raw token against stored sha256 digests.

    Hashes the presented token and compares against every active stored digest
    with hmac.compare_digest (constant time). Returns the matching token row
    dict, or None when no active token matches. Non-hashed (legacy plaintext)
    rows are never matched — run migrate_plaintext_api_tokens() first.
    """
    if not raw_token:
        return None
    presented_hash = hash_api_token(raw_token)
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM api_tokens WHERE is_active = 1").fetchall()
    finally:
        conn.close()
    for row in rows:
        stored = row["token"]
        if stored and _is_hashed_token(stored) and hmac.compare_digest(stored, presented_hash):
            return dict(row)
    return None


def migrate_plaintext_api_tokens() -> int:
    """One-shot upgrade: hash any plaintext api_tokens.token values.

    Detects rows whose stored token is NOT a 64-char hex digest (i.e. the old
    ``rmi_*`` plaintext format), replaces the value with sha256(token) and
    stores the first ``API_TOKEN_PREFIX_LEN`` chars as a non-sensitive
    ``token_prefix``. Returns the number of rows migrated.

    Idempotent: once a row is hashed it is left untouched on later runs, so
    this is safe to call on every bot start.
    """
    conn = get_db()
    try:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(api_tokens)").fetchall()]
        if "token_prefix" not in cols:
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute("ALTER TABLE api_tokens ADD COLUMN token_prefix TEXT DEFAULT ''")
                conn.commit()
        rows = conn.execute(
            "SELECT id, token FROM api_tokens WHERE token IS NOT NULL AND token != ''"
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return 0  # api_tokens table doesn't exist yet

    migrated = 0
    for row in rows:
        stored = row["token"]
        if _is_hashed_token(stored):
            continue
        # Legacy plaintext — capture a prefix before overwriting the raw secret.
        prefix = token_prefix(stored)
        conn.execute(
            "UPDATE api_tokens SET token = ?, token_prefix = ? WHERE id = ?",
            (hash_api_token(stored), prefix, row["id"]),
        )
        migrated += 1
    if migrated:
        conn.commit()
    conn.close()
    return migrated
