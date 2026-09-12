"""fix-web-knowledge-fallback.py idempotency on a fixture."""

import pathlib
import subprocess
import sys
import tempfile

# Repo-relative: CI checks out under /workspace/... (a hardcoded host path
# broke this test in CI).
FIXER = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "fix-web-knowledge-fallback.py"


def _run_fixer(tmp: str) -> subprocess.CompletedProcess:
    """Run the migration script on a fixture file (test-controlled args only)."""
    return subprocess.run(  # noqa: S603 — args are sys.executable + pytest tmp path
        [
            sys.executable,
            str(FIXER),
            str(pathlib.Path(tmp)),
        ],
        capture_output=True,
        text=True,
    )


def test_fixer_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "web.py"
        src.write_text("""    from app.ai_router import chat_completion as _chat_completion

    messages: list[dict[str, str]] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    try:
        result = await asyncio.wait_for(
            _chat_completion(
                messages=messages,
                model=None,
                task_type="chat",
                temperature=0.5,
                max_tokens=400,
            ),
            timeout=KNOWLEDGE_FALLBACK_TIMEOUT,
        )
    except Exception as exc:
        logger.warning("web_search knowledge fallback LLM unavailable: %s", str(exc)[:160])
        return ""
    if not isinstance(result, dict):
        return ""
    content = result.get("content")
    answer = content.strip() if isinstance(content, str) else ""
    if not answer:
        return ""
    if answer.startswith("I couldn't find enough context"):
        return ""
    return answer
""")
        src.write_text(src.read_text())  # ensure it's written
        # First run
        result = _run_fixer(pathlib.Path(tmp) / "web.py")
        assert result.returncode == 0
        # Second run should be idempotent
        result2 = _run_fixer(pathlib.Path(tmp) / "web.py")
        assert result2.returncode == 0
        content = (pathlib.Path(tmp) / "web.py").read_text()
        assert "chat_with_fallback" in content
        assert "chat_completion" not in content
        assert "answer = await asyncio.wait_for" in content
