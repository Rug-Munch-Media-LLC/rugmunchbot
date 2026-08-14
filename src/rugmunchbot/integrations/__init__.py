"""RugMunchBot — integration adapters (OSS fail-open stubs).

These modules replace imports of PRIVATE rmi-backend glue (news ingestion,
headless Telegram clients, scanner engines, RAG rerankers, Ghost CMS, …)
with fail-open no-ops. Every function returns empty/safe values so the bot
keeps running without the private monolith; self-hosters can implement the
same module names against their own backends.

Private glue → OSS stub mapping (see docs/RUGMUNCHBOT-EXTRACTION-CHECKLIST.md §2.2):

    app.domains.news_ingest.*            → rugmunchbot.integrations.news_ingest.*
    app.domains.telegram.headless        → rugmunchbot.integrations.headless
    app.domains.telegram.voice_chat      → rugmunchbot.integrations.voice_chat
    app.domains.scanner.{service,engines,…} → rugmunchbot.integrations.scanner_*
    app.rag.{story_ingest,rerank}        → rugmunchbot.integrations.story_ingest / rerank
    app.catalog.*                        → rugmunchbot.integrations.catalog
    app.domains.alerts.whale_watch       → rugmunchbot.integrations.whale_watch
    app.databus.tg_channel_registry      → rugmunchbot.integrations.tg_channel_registry
"""
