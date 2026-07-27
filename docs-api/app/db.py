"""PostgreSQL connection boundary used by the DocPlane API."""
from __future__ import annotations

import os
from contextlib import contextmanager

import psycopg2

_DSN = {
    "host": os.environ.get("DB_HOST", "postgres"),
    "port": os.environ.get("DB_PORT", "5432"),
    "dbname": os.environ.get("DB_NAME", "docs"),
    "user": os.environ.get("DB_USER", "docs"),
    "password": os.environ.get("DB_PASS", ""),
}


@contextmanager
def get_conn():
    conn = psycopg2.connect(**_DSN)
    try:
        yield conn
    finally:
        conn.close()
