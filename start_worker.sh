#!/usr/bin/env sh
# Background worker entrypoint. Run this as a separate Railway service.
set -e

echo "==> Starting Celery worker"
exec celery -A app.worker.celery_app.celery_app worker --loglevel="${LOG_LEVEL:-info}"
