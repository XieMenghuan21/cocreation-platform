#!/usr/bin/env bash
set -e

echo "Running database migrations..."
python -m alembic upgrade head

echo "Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
