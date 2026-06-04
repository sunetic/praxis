#!/bin/sh
set -e
mkdir -p /app/data
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
