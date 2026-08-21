# rugmunchbot — Telegram bot (python-telegram-bot, long-polling)
FROM python:3.12-slim

# Non-root with explicit numeric UID (resolvable by orchestrators/k8s).
RUN groupadd -r bot && useradd -r -u 10002 -g bot -s /bin/false bot

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && pip check

USER 10002

# Observability thread serves /healthz on :9191 (RUGMUNCHBOT_METRICS_PORT).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:9191/healthz"]

CMD ["python", "-m", "rugmunchbot.bot"]
