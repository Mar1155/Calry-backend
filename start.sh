#!/usr/bin/env sh
# Production entrypoint used by Railway for both API and worker services.
# Set CALRY_PROCESS=worker on the worker service; default is API.
# `exec` hands PID 1 to the child process so Railway's SIGTERM reaches it.
set -e

APP_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$APP_ROOT"

if [ "${CALRY_PROCESS:-api}" = "worker" ]; then
  exec ./start_worker.sh
fi

echo "==> Applying database migrations"
attempt=1
max_attempts=5
until alembic -c "$APP_ROOT/alembic.ini" upgrade head; do
  if [ "$attempt" -ge "$max_attempts" ]; then
    echo "==> Migrations failed after ${max_attempts} attempts, giving up"
    exit 1
  fi
  echo "==> Migration attempt ${attempt} failed, retrying in 3s (likely a cold-start filesystem race)"
  attempt=$((attempt + 1))
  sleep 3
done

echo "==> Starting Uvicorn on 0.0.0.0:${PORT:-8000}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
