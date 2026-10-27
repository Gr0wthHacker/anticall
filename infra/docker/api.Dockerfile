# Build from repo root: docker build -f infra/docker/api.Dockerfile .
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system --gid 10001 callshield \
 && useradd --system --uid 10001 --gid callshield --home-dir /app --shell /usr/sbin/nologin callshield

WORKDIR /app
COPY apps/api/pyproject.toml ./pyproject.toml
COPY apps/api/src ./src
COPY apps/api/alembic ./alembic

RUN pip install ".[postgres]" \
 && mkdir -p /var/lib/callshield/evidence \
 && chown -R callshield:callshield /app /var/lib/callshield

USER callshield
EXPOSE 8000

# Assumes the API exposes GET /healthz. Adjust if the route differs.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"

# Assumes callshield.api.main:app. Adjust if the entrypoint differs.
CMD ["uvicorn", "callshield.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
