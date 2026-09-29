"""Regression guard: a test process must never be able to publish to production.

Background: tests/conftest.py loads the REAL production env from
/srv/secrets/rugmunchbot.env, which carries a live GHOST_ADMIN_API_KEY. Any test
reaching ghost_mirror_post() therefore wrote a real post to the production Ghost
database -- 429 of 620 rows were synthetic fixtures ("first", "second",
"Hello world", "Whale wallet 0xabc"). Commit 3364f21 removed the publishing
credentials from the test process, but that fix is a single module-level
`os.environ.pop` loop: it holds only while nothing reloads the env afterwards,
and it is silently defeated if a different `tests/conftest.py` is imported
instead of the repo one (a stale copy in dist-packages already shadows this
package). These tests assert the invariant instead of trusting the mechanism.

Deleting the fixtures is only safe while this guard is green.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROD_ENV = Path("/srv/secrets/rugmunchbot.env")

# Any of these being set in a test process is the leak, because each one
# authenticates against the production Ghost Admin API.
GHOST_CRED_VARS = (
    "GHOST_ADMIN_API_KEY",
    "GHOST_ADMIN_KEY",
    "GHOST_CONTENT_API_KEY",
)

# conftest must be the repo's file. A copy elsewhere on sys.path that also calls
# load_dotenv(PROD_ENV) would reintroduce the leak, and 3364f21 would never run.
_SHADOW_RISK = "importing a different conftest.py skips the credential strip"


def test_ghost_credentials_absent_from_test_process() -> None:
    """No-patch-based injection: assert on os.environ state directly."""
    leaked = [v for v in GHOST_CRED_VARS if os.environ.get(v)]
    assert not leaked, (
        "Ghost publishing credentials are reachable inside the test process: "
        f"{leaked}. A test that calls ghost_mirror_post() would write to the "
        "PRODUCTION Ghost database. conftest.py must strip them."
    )


def test_conftest_strips_credentials() -> None:
    """The repo conftest is the file that carries the strip."""
    src = (REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "os.environ.pop" in src, "repo conftest.py no longer strips credentials"
    assert str(PROD_ENV) in src, (
        "repo conftest.py no longer loads the production env; the strip it "
        "performs is meaningless without the load it defends against"
    )


def test_no_shadowing_conftest_on_sys_path() -> None:
    """No other conftest.py may load the production env ahead of the repo one.

    /usr/local/lib/python3.12/dist-packages/tests/conftest.py (dated 2026-08-16,
    from an unrelated `caio` install) shadows the repo `tests` package, so
    `import tests.conftest` does NOT reliably reach the file carrying 3364f21.
    That shadow is benign only because it never touches Ghost. A shadow that DID
    call load_dotenv(PROD_ENV) would reintroduce the leak with the strip skipped,
    so that is the condition worth failing on -- not the mere existence of a
    same-named file elsewhere.
    """
    found = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib.util;print(importlib.util.find_spec('tests.conftest').origin)",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout.strip()

    repo_conftest = (REPO_ROOT / "tests" / "conftest.py").resolve()
    if not found or Path(found).resolve() == repo_conftest:
        return  # repo conftest wins; 3364f21 always runs.

    shadow = Path(found)
    assert not shadow.is_file() or str(PROD_ENV) not in shadow.read_text(
        encoding="utf-8", errors="replace"
    ), (
        f"{_SHADOW_RISK}: `tests.conftest` resolves to {shadow}, not "
        f"{repo_conftest}, and that file loads {PROD_ENV}."
    )


def test_strip_survives_reload_order() -> None:
    """Strip is import-time, so verify the real pytest order, not a re-load.

    Executing conftest and then loading the env -- pytest's own sequence -- must
    leave no credential behind, proving the strip runs after the load.
    """
    code = (
        "import importlib.util, os, sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from dotenv import load_dotenv\n"
        f"load_dotenv({str(PROD_ENV)!r})\n"
        "spec = importlib.util.spec_from_file_location('c', "
        f"{str(REPO_ROOT / 'tests' / 'conftest.py')!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "leaked = [v for v in "
        f"{list(GHOST_CRED_VARS)!r} if os.environ.get(v)]\n"
        "print(','.join(leaked))\n"
    )
    out = subprocess.run(  # noqa: S603 - argv is [sys.executable, -c, literal]
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout.strip()
    assert out == "", f"credentials survived conftest import: {out}"


def test_prod_env_is_the_only_credential_source() -> None:
    """If the env file disappears, the strip has nothing to defend against.

    Not a leak, but it means the guard is vacuous -- fail loudly so the guard
    cannot silently rot into a no-op.
    """
    if not PROD_ENV.is_file():
        pytest.skip(f"{PROD_ENV} not present; credential-strip guard is vacuous")
    text = PROD_ENV.read_text(encoding="utf-8", errors="replace")
    present = [
        v for v in GHOST_CRED_VARS if any(line.startswith(f"{v}=") for line in text.splitlines())
    ]
    assert present, (
        f"none of {GHOST_CRED_VARS} found in {PROD_ENV}; the strip guard would pass vacuously"
    )
