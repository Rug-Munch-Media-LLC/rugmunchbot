"""CLI entry point: ``python -m rugmunchbot.tools.content_lint <file-or-text>``.

Thin shim over :mod:`rugmunchbot.support.content_lint` (the canonical,
pure-python lint). Kept in ``tools`` so the documented command works; the
implementation lives in ``support`` because the import-linter contract
forbids ``tools`` from importing ``channels``.

Exit codes: 0 clean, 1 violations, 2 usage/IO error.
"""

from __future__ import annotations

from rugmunchbot.support.content_lint import main

if __name__ == "__main__":
    raise SystemExit(main())
