#!/usr/bin/env python3
"""Phase 2b documented deviation: swap tools/web.py's knowledge-fallback LLM
from ``app.ai_router.chat_completion`` (rmi-backend) to the bot-local
``rugmunchbot.llm_config.chat_with_fallback`` (OpenRouter snappy -> :lowest).

Idempotent. Called by scripts/vendor-from-backend.sh after every re-vendor.
"""

import pathlib
import sys

WEB = (
    pathlib.Path(sys.argv[1])
    if len(sys.argv) > 1
    else pathlib.Path(__file__).resolve().parent.parent / "src" / "rugmunchbot" / "tools" / "web.py"
)
raw = WEB.read_text()

OLD_IMPORT = "    from app.ai_router import chat_completion as _chat_completion"
NEW_IMPORT = "    from rugmunchbot.llm_config import chat_with_fallback"

if OLD_IMPORT in raw:
    raw = raw.replace(OLD_IMPORT, NEW_IMPORT)

    OLD_CALL = (
        "        result = await asyncio.wait_for(\n"
        "            _chat_completion(\n"
        "                messages=messages,\n"
        "                model=None,\n"
        '                task_type="chat",\n'
        "                temperature=0.5,\n"
        "                max_tokens=400,\n"
        "            ),\n"
        "            timeout=KNOWLEDGE_FALLBACK_TIMEOUT,\n"
        "        )"
    )
    NEW_CALL = (
        "        answer = await asyncio.wait_for(\n"
        '            chat_with_fallback(messages=messages, tier="snappy", max_tokens=400),\n'
        "            timeout=KNOWLEDGE_FALLBACK_TIMEOUT,\n"
        "        )"
    )
    if OLD_CALL not in raw:
        raise SystemExit("web.py: chat_completion call block not found")
    raw = raw.replace(OLD_CALL, NEW_CALL)

    OLD_PARSE = (
        '    if not isinstance(result, dict):\n        return ""\n'
        '    content = result.get("content")\n'
        '    answer = content.strip() if isinstance(content, str) else ""'
    )
    NEW_PARSE = (
        '    if not isinstance(answer, str):\n        return ""\n    answer = answer.strip()'
    )
    if OLD_PARSE not in raw:
        raise SystemExit("web.py: result-parse block not found")
    raw = raw.replace(OLD_PARSE, NEW_PARSE)

    WEB.write_text(raw)
    print("web.py: knowledge fallback -> bot-local llm_config")
elif "chat_with_fallback(messages=messages" not in raw:
    raise SystemExit("web.py: ai_router import neither old nor new form")
else:
    print("web.py: already bot-local (idempotent)")
