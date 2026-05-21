#!/usr/bin/env bash
# Container entrypoint: bootstrap CTFd state, then start the web server.
set -euo pipefail

cd /opt/CTFd

echo "[entrypoint] Running bootstrap..."
python /opt/econ-judge/bin/bootstrap.py

PORT="${PORT:-8000}"
WORKERS="${WEB_WORKERS:-1}"
WORKER_CLASS="${WEB_WORKER_CLASS:-gevent}"

echo "[entrypoint] Starting CTFd on :${PORT} (gunicorn, ${WORKER_CLASS}, ${WORKERS} workers)..."
exec gunicorn \
    --bind "0.0.0.0:${PORT}" \
    --workers "${WORKERS}" \
    --worker-class "${WORKER_CLASS}" \
    --timeout 60 \
    --access-logfile - \
    --error-logfile - \
    "CTFd:create_app()"
