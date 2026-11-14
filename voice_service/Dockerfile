FROM python:3.12-slim

# HOST=0.0.0.0 inside the container only: publish the port on loopback
# (-p 127.0.0.1:8080:8080) and terminate TLS in front (deploy/Caddyfile).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app
COPY requirements.txt pyproject.toml README.md ./
COPY excom_voice ./excom_voice
RUN pip install -r requirements.txt && pip install --no-deps . && useradd --system --no-create-home excom-voice
USER excom-voice

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/health', timeout=2)"
# SIGTERM stops new calls, waits for live ones (at most max_seconds, 900 at the
# Frappe side's ceiling) and then for their end reports (END_RETRY_SECONDS, 120):
#   docker run --stop-timeout 1200 ...   (or stop_grace_period: 20m in compose)
CMD ["excom-voice"]
