# Build context: repository root (needs ../shared).
#   docker build -f backend/Dockerfile -t tmusic-backend .
# One image, three roles (set by the compose `command`):
#   api     uvicorn app.api.main:create_app --factory ...
#   worker  arq app.workers.main.WorkerSettings
#   migrate alembic upgrade head

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_SYSTEM_PYTHON=1
RUN pip install --no-cache-dir "uv==0.5.*" \
 && groupadd --system app && useradd --system --gid app --home /srv app
WORKDIR /srv

FROM base AS deps
COPY backend/requirements.lock /tmp/requirements.lock
RUN uv pip install --no-cache -r /tmp/requirements.lock

FROM deps AS runtime
COPY shared /srv/shared
RUN uv pip install --no-cache --no-deps /srv/shared
COPY backend /srv/backend
WORKDIR /srv/backend
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "app.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header", "--workers", "2"]
