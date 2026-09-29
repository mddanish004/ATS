# HireFlow API — production image.
#
# Build from the repository root so compose paths stay consistent:
#   docker build -f apps/api/Dockerfile .
#
# Dependencies are baked in at build time (uv sync --frozen); the
# container never installs packages on startup. No secrets are baked in:
# runtime configuration comes from the environment (see .env.example).
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app/apps/api

RUN pip install --no-cache-dir uv

COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY apps/api/alembic.ini ./
COPY apps/api/migrations ./migrations
COPY apps/api/app ./app

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p var/private_uploads \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Single-release strategy: migrate, then start the server. For
# multi-replica deploys, run migrations as a separate release step
# instead of in the container command.
CMD ["sh", "-c", ".venv/bin/alembic upgrade head && exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://localhost:' + os.environ.get('PORT', '8000') + '/health')"
