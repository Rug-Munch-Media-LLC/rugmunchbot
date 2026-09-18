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

## X/Twitter intel — moved to rugbrain (RAG)

X ingestion (fetch, normalize, embed, ingest) is owned by **rugbrain** (the
RAG service). The bot no longer implements X fetching, embeddings, ingest
pipelines, or Qdrant internals — the ApiPass and GetXAPI rails are retired.

The bot may **consume** X-derived intel through the RAG HTTP API
(`tools/rag_lookup.py`). `/tweet`, `read_tweet`, and `story_fetch` fail open
with an honest "X reading has moved" note and never scrape X directly.

See `docs/X-INTEL-MOVED.md`.

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
