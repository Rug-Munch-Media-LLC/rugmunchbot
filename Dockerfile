# rugmunchbot — Telegram bot (python-telegram-bot, long-polling)
FROM python:3.12-slim

# Non-root with explicit numeric UID (resolvable by orchestrators/k8s).
RUN groupadd -r bot && useradd -r -u 10002 -g bot -s /bin/false bot

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && pip check

USER 10002

# Long-polling bot: no HTTP listener, so no HEALTHCHECK — restart policy in
# compose is the recovery mechanism. An internal health thread is a follow-up.
CMD ["python", "-m", "rugmunchbot.bot"]
