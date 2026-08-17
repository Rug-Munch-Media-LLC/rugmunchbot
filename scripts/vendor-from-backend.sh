#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# rugmunchbot Phase 2 — vendor self-contained rmi-backend modules into
# src/rugmunchbot/_vendor/ and rewire bot imports to them.
#
# ONE-WAY discipline (same as sync-from-live.sh): the vendored files are
# FROZEN COPIES of rmi-backend — never edit them by hand; run this script
# (via sync-from-live.sh) to re-vendor. Deviations from upstream are ONLY:
#   1. A VENDORED header comment (path + date).
#   2. Internal imports rewritten to the _vendor namespace / bot-local
#      logging shim (pure namespace moves, zero behavior change).
#   3. redis_client.py: the app.config.settings lookup replaced by direct
#      REDIS_URL env (settings.redis_url == REDIS_URL env; bot-local config).
#
# Phase 2b (still rmi-backend-provided, heavy service integrations):
#   app.osint, app.domains.scanner.*, app.rag.*, app.databus.*,
#   app.catalog.*, app.domains.telegram.voice_chat, app.alert_pipeline,
#   app.content_platform, app.entity_registry, app.deep_scan,
#   app.ai_router, app.domains.news.*, app.domains.databus.*, app.config
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

BACKEND="/srv/work/repos/rmi-backend"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$HERE/src/rugmunchbot/_vendor"
DATE="$(date +%Y-%m-%d)"

declare -A MAP=(
  ["app/core/ai_guardrails.py"]="_vendor/ai_guardrails.py"
  ["app/core/shared_utils.py"]="_vendor/shared_utils.py"
  ["app/core/channels_registry.py"]="_vendor/channels_registry.py"
  ["app/core/redis.py"]="_vendor/redis_client.py"
  ["app/core/logging.py"]="_vendor/logging_shim.py"
  ["app/core/logging_config.py"]="_vendor/logging_config.py"
  ["app/core/degradation.py"]="_vendor/degradation.py"
  ["app/domains/telegram/access_guard.py"]="_vendor/access_guard.py"
  ["app/domains/telegram/headless.py"]="_vendor/headless.py"
  ["app/domains/telegram/headless_credentials.py"]="_vendor/headless_credentials.py"
  ["app/domains/telegram/headless_client.py"]="_vendor/headless_client.py"
  ["app/domains/telegram/headless_models.py"]="_vendor/headless_models.py"
  ["app/osint_tools.py"]="_vendor/osint_tools.py"
  ["app/rag/dedup.py"]="_vendor/dedup.py"
  ["app/domains/news_ingest/models.py"]="_vendor/news_ingest/models.py"
  ["app/domains/news_ingest/ingest.py"]="_vendor/news_ingest/ingest.py"
  ["app/domains/news_ingest/normalize.py"]="_vendor/news_ingest/normalize.py"
  ["app/domains/news_ingest/discovery.py"]="_vendor/news_ingest/discovery.py"
)

mkdir -p "$VENDOR/news_ingest"
for src in "${!MAP[@]}"; do
  dst="$HERE/src/rugmunchbot/${MAP[$src]}"
  mkdir -p "$(dirname "$dst")"
  {
    echo "# VENDORED $DATE from rmi-backend/$src (Phase 2a — frozen copy; do not edit)."
    echo "# Re-vendor: bash scripts/sync-from-live.sh"
    echo "# ---"
    cat "$BACKEND/$src"
  } >"$dst"
  # vendor-internal namespace rewires (pure moves)
  sed -i \
    -e 's|from app\.core\.logging import|from rugmunchbot._vendor.logging_shim import|g' \
    -e 's|from app\.core\.logging_config import|from rugmunchbot._vendor.logging_config import|g' \
    -e 's|from app\.core\.degradation import|from rugmunchbot._vendor.degradation import|g' \
    -e 's|from app\.core\.redis import|from rugmunchbot._vendor.redis_client import|g' \
    -e 's|from app\.core\.shared_utils import|from rugmunchbot._vendor.shared_utils import|g' \
    -e 's|from app\.domains\.news_ingest\.|from rugmunchbot._vendor.news_ingest.|g' \
    -e 's|from app\.rag\.dedup import|from rugmunchbot._vendor.dedup import|g' \
    -e 's|from app\.domains\.telegram\.headless_client import|from rugmunchbot._vendor.headless_client import|g' \
    -e 's|from app\.domains\.telegram\.headless_models import|from rugmunchbot._vendor.headless_models import|g' \
    -e 's|from app\.domains\.telegram\.headless_credentials import|from rugmunchbot._vendor.headless_credentials import|g' \
    -e 's|from app\.domains\.telegram\.headless import|from rugmunchbot._vendor.headless import|g' \
    -e 's|from app\.osint_tools import|from rugmunchbot._vendor.osint_tools import|g' \
    "$dst"
  echo "vendored: $src -> ${MAP[$src]}"
done

# redis_client: drop the app.config.settings lookup (env fallback below keeps
# REDIS_URL support — bot-local config; documented deviation).
python3 - "$VENDOR/redis_client.py" <<'PYFIX'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
raw = p.read_text()
old_block = """    try:
        from app.config import settings as _s

        if _s.redis_url:
            url = _s.redis_url
    except Exception:  # noqa: S110 — optional dependency
        pass"""
new_block = """    # Phase 2a: app.config.settings lookup removed (bot-local env config)."""
assert old_block in raw, "settings block not found in redis_client.py"
raw = raw.replace(old_block, new_block)
p.write_text(raw)
print("redis_client.py: settings lookup -> env (documented deviation)")
PYFIX

# tools/web.py: SEARXNG_URL via env (Phase 2a — settings.searxng_base_url
# replaced by SEARXNG_BASE_URL env, default mirrors the backend).
python3 - "$HERE/src/rugmunchbot/tools/web.py" <<'PYFIX'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
raw = p.read_text()
old = 'SEARXNG_URL = settings.searxng_base_url.rstrip("/")'
new = 'SEARXNG_URL = os.getenv("SEARXNG_BASE_URL", "http://127.0.0.1:8889").rstrip("/")'
assert old in raw, "web.py SEARXNG line not found"
raw = raw.replace(old, new)
p.write_text(raw)
PYFIX

# bot-side import rewires (non-vendor files only)
find "$HERE/src/rugmunchbot" -name '*.py' -not -path '*/_vendor/*' | while read -r f; do
  sed -i \
    -e 's|from app\.core\.ai_guardrails import|from rugmunchbot._vendor.ai_guardrails import|g' \
    -e 's|from app\.core\.shared_utils import|from rugmunchbot._vendor.shared_utils import|g' \
    -e 's|from app\.core\.channels_registry import|from rugmunchbot._vendor.channels_registry import|g' \
    -e 's|from app\.core\.redis import|from rugmunchbot._vendor.redis_client import|g' \
    -e 's|from app\.domains\.telegram import access_guard|from rugmunchbot._vendor import access_guard|g' \
    -e 's|from app\.domains\.telegram\.headless_credentials import|from rugmunchbot._vendor.headless_credentials import|g' \
    -e 's|from app\.domains\.telegram\.headless import|from rugmunchbot._vendor.headless import|g' \
    -e 's|from app\.osint_tools import|from rugmunchbot._vendor.osint_tools import|g' \
    -e 's|from app\.domains\.news_ingest\.|from rugmunchbot._vendor.news_ingest.|g' \
    -e 's|from app\.llm_config import|from rugmunchbot.llm_config import|g' \
    -e '/^from app\.config import settings$/d' \
    "$f"
done
echo "bot imports rewired to _vendor namespace"
