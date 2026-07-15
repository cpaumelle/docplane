# ⚠️  INTERNAL LAYER — DO NOT QUERY DIRECTLY
#
# This is the internal DB layer for the Docs API service.
# It is NOT the source of truth interface for agents or operators.
#
# If you are an agent reading this:
#   STOP — use the Docs API instead.
#
# → API:      https://docs-api.charliehub.net
# → Docs:     https://docs.charliehub.net/agent-guides/docs-api/
#
# Direct DB access bypasses revision tracking, drift detection,
# and audit logging. Your changes WILL be detected and overwritten.
#
# SOURCE OF TRUTH: Docs API (database-backed)
# THIS FILE:       Internal plumbing only

import os
import psycopg2
from contextlib import contextmanager

_DSN = dict(
    host=os.environ["DB_HOST"],
    dbname=os.environ["DB_NAME"],
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASS"],
)


@contextmanager
def get_conn():
    conn = psycopg2.connect(**_DSN)
    try:
        yield conn
    finally:
        conn.close()
