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
  ["app/alert_pipeline.py"]="_vendor/alert_pipeline.py"
  ["app/content_platform.py"]="_vendor/content_platform.py"
  ["app/entity_registry.py"]="_vendor/entity_registry.py"
  ["app/domains/telegram/voice_chat.py"]="_vendor/voice_chat.py"
  ["app/databus/tg_channel_registry.py"]="_vendor/tg_channel_registry.py"
  ["app/databus/proxy_rotator.py"]="_vendor/proxy_rotator.py"
  ["app/databus/rugcharts_intel/smart_money.py"]="_vendor/smart_money.py"
  ["app/databus/provider_implementations/dune.py"]="_vendor/dune.py"
  ["app/domains/news/clusterer.py"]="_vendor/news_clusterer.py"
  ["app/domains/news/ratings.py"]="_vendor/news_ratings.py"
  ["app/deep_scan.py"]="_vendor/deep_scan.py"
  ["app/deep_scan_runners.py"]="_vendor/deep_scan_runners.py"
  ["app/databus/webshare_proxies.py"]="_vendor/webshare_proxies.py"
  ["app/domains/content/briefing.py"]="_vendor/briefing.py"
  ["app/core/db_pool.py"]="_vendor/db_pool.py"
)

rewrite_vendor() {
  local f="$1"
  sed -i \
    -e 's|from app\.core\.logging import|from rugmunchbot._vendor.logging_shim import|g' \
    -e 's|from app\.core\.logging_config import|from rugmunchbot._vendor.logging_config import|g' \
    -e 's|from app\.core\.degradation import|from rugmunchbot._vendor.degradation import|g' \
    -e 's|from app\.core\.redis import|from rugmunchbot._vendor.redis_client import|g' \
    -e 's|from app\.core\.shared_utils import|from rugmunchbot._vendor.shared_utils import|g' \
    -e 's|from app\.chain_registry\.|from rugmunchbot._vendor.chain_registry.|g' \
    -e 's|from app\.chain_registry import|from rugmunchbot._vendor.chain_registry import|g' \
    -e 's|from app\.deep_scan_runners import|from rugmunchbot._vendor.deep_scan_runners import|g' \
    -e 's|from app\.domains\.news\.ratings import|from rugmunchbot._vendor.news_ratings import|g' \
    -e 's|from app\.domains\.news\.clusterer import|from rugmunchbot._vendor.news_clusterer import|g' \
    -e 's|from app\.osint\.|from rugmunchbot._vendor.osint.|g' \
    -e 's|from app\.osint import|from rugmunchbot._vendor.osint import|g' \
    -e 's|from app\.domains\.content\.briefing import|from rugmunchbot._vendor.briefing import|g' \
    -e 's|from app\.core\.db_pool import|from rugmunchbot._vendor.db_pool import|g' \
    -e 's|from app\.databus\.webshare_proxies import|from rugmunchbot._vendor.webshare_proxies import|g' \
    -e 's|from app\.domains\.news_ingest\.|from rugmunchbot._vendor.news_ingest.|g' \
    -e 's|from app\.rag\.dedup import|from rugmunchbot._vendor.dedup import|g' \
    -e 's|from app\.domains\.telegram\.headless_client import|from rugmunchbot._vendor.headless_client import|g' \
    -e 's|from app\.domains\.telegram\.headless_models import|from rugmunchbot._vendor.headless_models import|g' \
    -e 's|from app\.domains\.telegram\.headless_credentials import|from rugmunchbot._vendor.headless_credentials import|g' \
    -e 's|from app\.domains\.telegram\.headless import|from rugmunchbot._vendor.headless import|g' \
    -e 's|from app\.osint_tools import|from rugmunchbot._vendor.osint_tools import|g' \
    "$f"
}

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
  rewrite_vendor "$dst"
    echo "vendored: $src -> ${MAP[$src]}"
done

# package copies (whole dirs, self-contained)
for pkg_src in "app/osint" "app/chain_registry"; do
  pkg_dst="$VENDOR/$(basename "$pkg_src")"
  mkdir -p "$pkg_dst"
  ( cd "$BACKEND" && find "$pkg_src" -name '*.py' -not -path '*__pycache__*' | while read -r f; do
      rel="${f#$pkg_src/}"
      mkdir -p "$pkg_dst/$(dirname "$rel")"
      { echo "# VENDORED $DATE from rmi-backend/$f (Phase 2b — frozen copy; do not edit)."
        echo "# Re-vendor: bash scripts/sync-from-live.sh"
        echo "# ---"
        cat "$f"
      } > "$pkg_dst/$rel"
      rewrite_vendor "$pkg_dst/$rel"
    done )
  echo "vendored package: $pkg_src -> _vendor/$(basename "$pkg_src")"
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

# tools/web.py: SEARXNG_URL via env (Phase 2a) + knowledge-fallback LLM
# swapped from app.ai_router.chat_completion to the bot-local
# rugmunchbot.llm_config.chat_with_fallback (Phase 2b).
python3 "$HERE/scripts/fix-web-knowledge-fallback.py" "$HERE/src/rugmunchbot/tools/web.py"
python3 - "$HERE/src/rugmunchbot/tools/web.py" <<'PYFIX'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
raw = p.read_text()
old = 'SEARXNG_URL = settings.searxng_base_url.rstrip("/")'
new = 'SEARXNG_URL = os.getenv("SEARXNG_BASE_URL", "http://127.0.0.1:8889").rstrip("/")'
if old in raw:
    raw = raw.replace(old, new)
elif new not in raw:
    raise SystemExit("web.py SEARXNG line not found (neither old nor new form)")
p.write_text(raw)
PYFIX

# bot-side import rewires (non-vendor files only)
find "$HERE/src/rugmunchbot" -name '*.py' -not -path '*/_vendor/*' | while read -r f; do
  sed -i \
    -e 's|from app\.core\.ai_guardrails import|from rugmunchbot._vendor.ai_guardrails import|g' \
    -e 's|from app\.core\.shared_utils import|from rugmunchbot._vendor.shared_utils import|g' \
    -e 's|from app\.chain_registry\.|from rugmunchbot._vendor.chain_registry.|g' \
    -e 's|from app\.chain_registry import|from rugmunchbot._vendor.chain_registry import|g' \
    -e 's|from app\.deep_scan_runners import|from rugmunchbot._vendor.deep_scan_runners import|g' \
    -e 's|from app\.domains\.news\.ratings import|from rugmunchbot._vendor.news_ratings import|g' \
    -e 's|from app\.domains\.news\.clusterer import|from rugmunchbot._vendor.news_clusterer import|g' \
    -e 's|from app\.osint\.|from rugmunchbot._vendor.osint.|g' \
    -e 's|from app\.osint import|from rugmunchbot._vendor.osint import|g' \
    -e 's|from app\.domains\.content\.briefing import|from rugmunchbot._vendor.briefing import|g' \
    -e 's|from app\.core\.db_pool import|from rugmunchbot._vendor.db_pool import|g' \
    -e 's|from app\.databus\.webshare_proxies import|from rugmunchbot._vendor.webshare_proxies import|g' \
    -e 's|from app\.domains\.content\.briefing import|from rugmunchbot._vendor.briefing import|g' \
    -e 's|from app\.core\.db_pool import|from rugmunchbot._vendor.db_pool import|g' \
    -e 's|from app\.databus\.webshare_proxies import|from rugmunchbot._vendor.webshare_proxies import|g' \
    -e 's|from app\.core\.channels_registry import|from rugmunchbot._vendor.channels_registry import|g' \
    -e 's|from app\.core\.redis import|from rugmunchbot._vendor.redis_client import|g' \
    -e 's|from app\.domains\.telegram import access_guard|from rugmunchbot._vendor import access_guard|g' \
    -e 's|from app\.domains\.telegram\.headless_credentials import|from rugmunchbot._vendor.headless_credentials import|g' \
    -e 's|from app\.domains\.telegram\.headless import|from rugmunchbot._vendor.headless import|g' \
    -e 's|from app\.osint_tools import|from rugmunchbot._vendor.osint_tools import|g' \
    -e 's|from app\.domains\.news_ingest\.|from rugmunchbot._vendor.news_ingest.|g' \
    -e 's|from app\.llm_config import|from rugmunchbot.llm_config import|g' \
    -e 's|from app\.alert_pipeline import|from rugmunchbot._vendor.alert_pipeline import|g' \
    -e 's|from app\.content_platform import|from rugmunchbot._vendor.content_platform import|g' \
    -e 's|from app\.entity_registry import|from rugmunchbot._vendor.entity_registry import|g' \
    -e 's|from app\.domains\.telegram\.voice_chat import|from rugmunchbot._vendor.voice_chat import|g' \
    -e 's|from app\.databus\.tg_channel_registry import|from rugmunchbot._vendor.tg_channel_registry import|g' \
    -e 's|from app\.databus\.proxy_rotator import|from rugmunchbot._vendor.proxy_rotator import|g' \
    -e 's|from app\.databus\.rugcharts_intel\.smart_money import|from rugmunchbot._vendor.smart_money import|g' \
    -e 's|from app\.databus\.provider_implementations\.dune import|from rugmunchbot._vendor.dune import|g' \
    -e 's|from app\.domains\.news\.clusterer import|from rugmunchbot._vendor.news_clusterer import|g' \
    -e 's|from app\.domains\.news\.ratings import|from rugmunchbot._vendor.news_ratings import|g' \
    -e 's|from app\.chain_registry import|from rugmunchbot._vendor.chain_registry import|g' \
    -e 's|from app\.deep_scan import|from rugmunchbot._vendor.deep_scan import|g' \
    -e 's|from app\.osint\.|from rugmunchbot._vendor.osint.|g' \
    -e 's|from app\.osint import|from rugmunchbot._vendor.osint import|g' \
    -e 's|from app\.domains\.content\.briefing import|from rugmunchbot._vendor.briefing import|g' \
    -e 's|from app\.core\.db_pool import|from rugmunchbot._vendor.db_pool import|g' \
    -e 's|from app\.databus\.webshare_proxies import|from rugmunchbot._vendor.webshare_proxies import|g' \
    -e 's|from app\.domains\.content\.briefing import|from rugmunchbot._vendor.briefing import|g' \
    -e 's|from app\.core\.db_pool import|from rugmunchbot._vendor.db_pool import|g' \
    -e 's|from app\.databus\.webshare_proxies import|from rugmunchbot._vendor.webshare_proxies import|g' \
    -e '/^from app\.config import settings$/d' \
    "$f"
done
echo "bot imports rewired to _vendor namespace"
