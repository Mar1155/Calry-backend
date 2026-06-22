#!/usr/bin/env sh
# Production entrypoint: apply DB migrations, then serve.
# `exec` hands PID 1 to uvicorn so Railway's SIGTERM reaches it for clean shutdown.
set -e

echo "==> Applying database migrations (alembic upgrade head)"
alembic upgrade head

echo "==> Starting Uvicorn on 0.0.0.0:${PORT:-8000}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
