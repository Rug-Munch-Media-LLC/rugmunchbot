# RugMunchBot — Troubleshooting Guide

## Common Issues

### Bot won't start

**Symptom:** `systemctl start rugmunchbot` fails or bot exits immediately.

**Check logs:**
```bash
journalctl -u rugmunchbot --since "5 minutes ago" --no-pager
```

**Common causes:**

1. **Missing bot token**
   ```
   ERROR: BOT_TOKEN is empty
   ```
   **Fix:** Ensure gopass has the token:
   ```bash
   gopass show [INTERNAL_SECRET_PATH]
   ```

2. **Backend unreachable**
   ```
   WARNING: health_check_failed_at_startup component=backend
   ```
   **Fix:** Backend health check is fail-open — bot will still start but some features may be limited.

3. **RAG/Qdrant unreachable**
   ```
   WARNING: health_check_failed_at_startup component=qdrant
   ```
   **Fix:** RAG is fail-open — bot skips memory indexing when Qdrant is down. Check Qdrant:
   ```bash
   curl http://localhost:6333/health
   ```

4. **Scanner RPC unhealthy**
   ```
   WARNING: scanner_health_check_no_healthy_endpoint chain=ethereum
   ```
   **Fix:** Scanner falls back to public RPCs. Check RPC endpoints in `lite_indexer/chain_registry.py`.

### Scan returns no data

**Symptom:** `/scan 0xabc` returns "no data" or empty report.

**Check:**
```bash
# Test backend directly
curl -H "X-API-Key: $(gopass show [INTERNAL_SECRET_PATH])" \
  -X POST http://localhost:8000/api/v1/scanner/token/scan \
  -d '{"address": "0xabc", "chain": "ethereum"}'
```

**Common causes:**
1. Backend scanner engine down
2. Token address is invalid or not on supported chain
3. RPC endpoints rate-limited

### Webhook delivery failures

**Symptom:** Webhook subscribers not receiving events.

**Check metrics:**
```bash
curl http://localhost:8095/metrics | grep webhook
```

**Common causes:**
1. Subscriber URL unreachable
2. Signature verification failing (check secret matches)
3. Rate limiting (check `rmi_bot_webhook_failures_total`)

**Check dead letter queue:**
```python
from rugmunchbot.webhook_manager import DeadLetterQueue
# Failed deliveries are stored in memory — restart clears them
```

### High memory usage

**Symptom:** Bot RSS memory exceeds 2GB.

**Check:**
```bash
systemctl status rugmunchbot | grep Memory
```

**Common causes:**
1. Too many concurrent chains indexing
2. Large batch sizes
3. Memory leak in plugins

**Fix:**
- Reduce `MAX_CONCURRENT_CHAINS` in config
- Reduce `BATCH_SIZE` per chain
- Check for plugin leaks with memory profiling

### Channel jobs not posting

**Symptom:** Scheduled channel posts not appearing.

**Check logs:**
```bash
journalctl -u rugmunchbot --since "1 hour ago" | grep -E "channel|post|publish"
```

**Common causes:**
1. Channel ID not configured in `CHANNELS` env var
2. Bot lacks permissions in the channel
3. Content pipeline failing validation

### Qdrant sparse vector warning

**Symptom:** `Wrong input: Not existing vector name error: sparse` in logs.

**This is a pre-existing warning** — the `rag_documents` collection was created with a sparse vector configuration that doesn't match the current embedder. It does not affect bot functionality.

**Fix (optional):** Recreate the collection with correct vector config:
```python
# WARNING: This will delete all indexed data
client.recreate_collection(
    collection_name="rag_documents", vectors_config={"size": 2560, "distance": "Cosine"}
)
```

## ApiPass X rail (operator provisioning)

The bot reads X posts EXCLUSIVELY through the ApiPass X source
(`src/rugmunchbot/tools/apipass_x.py`) — no direct scraping of x.com pages.
All X consumers share one hard budget of ≤6 task-creations/hr
(`RMI_APIPASS_MAX_CALLS_PER_HOUR`); polling `recordInfo` is free.

**Symptom:** `apipass_x:create_http status=404 err={"detail":"Model type not found or not configured: twitter-api-simple"}` in logs; `/tweet` replies "Can't read that X post right now"; channel X rails silently fall back to SearXNG.

**Verified 2026-08-29:** the key authenticates (no 401) and the model slug
`twitter-api-simple` + endpoints (`twitter/simple/{tweet-detail,user-tweets,search,quotes,retweeters}`) are confirmed against the live `/v1/models` catalog. The 404 means the model type is **not enabled on the account** — a provisioning step, not a code defect.

**Operator must:**
1. Sign in to the ApiPass dashboard (apipass.dev) with the account whose key is in the secret store (`apipass_api_key`) / gopass (`[INTERNAL_SECRET_PATH]`).
2. Models Market → search `twitter` → `twitter-api-simple` (Twitter brand, "social-media-api" task type) → enable/configure it for the API key. If it cannot be self-served, open a support ticket (ApiPass advertises 24/7 human support).
3. Make sure the account has credits for the route — 50 free credits ≈ 100 calls at ~0.5 credits/call; top up if the social-media-api channel needs a paid route.
4. Verify end-to-end: `scripts/verify_apipass.py` must print `=== REAL TWEET PULLED ===`. Until then everything fails open (the /tweet command, `read_tweet` tool, KOL scoreboard, content channel X rail) — a dead ApiPass never breaks a tick.

**Also available:** `/tweet <url|id|@handle>` and `/tweets <@handle>` commands, the chat AI `read_tweet` tool, and `story_fetch` refuses x.com URLs (routes them to ApiPass) so the generic crawler never scrapes X.

## Debug Mode

Enable verbose logging:
```bash
export RUGMUNCHBOT_LOG_LEVEL=DEBUG
systemctl restart rugmunchbot
```

## Getting Help

1. Check logs: `journalctl -u rugmunchbot -f`
2. Check metrics: `curl http://localhost:8095/metrics`
3. Check health: `curl http://localhost:8095/healthz`
4. Review audit report: `docs/AUDIT-2026-08-27.md`
5. Review enterprise quality report: `docs/ENTERPRISE-QUALITY-REPORT-2026-08-27.md`
