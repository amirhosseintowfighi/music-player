# Build context: repository root.
#   docker build -f indexer/Dockerfile -t tmusic-edge .
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 UV_SYSTEM_PYTHON=1
RUN pip install --no-cache-dir "uv==0.5.*" \
 && groupadd --system app && useradd --system --gid app --home /srv app \
 && mkdir -p /data/sessions && chown -R app:app /data
WORKDIR /srv

FROM base AS deps
COPY indexer/requirements.lock /tmp/requirements.lock
RUN uv pip install --no-cache -r /tmp/requirements.lock

FROM deps AS runtime
COPY shared /srv/shared
RUN uv pip install --no-cache --no-deps /srv/shared
COPY indexer /srv/indexer
WORKDIR /srv/indexer
USER app
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"
CMD ["python", "-m", "tmusic_indexer.main"]
