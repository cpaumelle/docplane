#!/bin/sh
set -eu

attempt=0
until python /app/migrate.py apply --dir /app/db/migrations; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "DocPlane migrations failed after $attempt attempts" >&2
    exit 1
  fi
  sleep 2
done

exec uvicorn app.application:app --host 0.0.0.0 --port 8010
