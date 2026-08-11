#!/usr/bin/env sh
# Background worker entrypoint. Run this as a separate Railway service.
set -e

echo "==> Starting Celery worker"
exec celery -A app.worker.celery_app.celery_app worker \
  --beat \
  --loglevel="${LOG_LEVEL:-info}" \
  --concurrency="${CELERY_WORKER_CONCURRENCY:-1}" \
  --prefetch-multiplier=1 \
  --max-tasks-per-child="${CELERY_WORKER_MAX_TASKS_PER_CHILD:-20}" \
  --max-memory-per-child="${CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB:-384000}"
