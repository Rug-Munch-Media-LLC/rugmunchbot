"""News-ingestion adapter package (OSS fail-open).

The private monolith reads Telegram channels and normalizes them into
``NewsArticle`` objects via ``app.domains.news_ingest.*``. OSS ships no-op
implementations: sweeps return ``[]``, normalization returns ``None``, and
dedupe passes through — so the channel jobs stay green without the private
pipeline.
"""
