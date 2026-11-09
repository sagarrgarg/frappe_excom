FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app
COPY pyproject.toml README.md ./
COPY excom_voice ./excom_voice
RUN pip install . && useradd --system --no-create-home excom
USER excom

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/health', timeout=2)"
# SIGTERM stops new calls and waits for live ones; give it time:
#   docker run --stop-timeout 960 ...   (or stop_grace_period in compose)
CMD ["excom-voice"]
