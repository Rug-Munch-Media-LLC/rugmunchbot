# X intel moved to rugbrain (RAG)

X/Twitter ingestion (fetch, normalize, embed, ingest) is now owned by
**rugbrain** (the RAG service). The bot no longer implements X fetching,
embeddings, ingest pipelines, or Qdrant internals.

The bot may **consume** X-derived intel through the RAG HTTP API
(`tools/rag_lookup.py`). There is no bot-side ApiPass/GetXAPI rail.
