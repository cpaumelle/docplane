"""Operational endpoints for the assembled DocPlane service."""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter

from app.db import get_conn
from app.runtime import certification_status

router = APIRouter(tags=["system"])


@router.get("/healthz")
def health() -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM docplane.schema_migrations")
        migration_count = int(cur.fetchone()[0])
        cur.execute("SELECT count(*) FROM docs.pages")
        page_count = int(cur.fetchone()[0])
    return {
        "status": "ok",
        "product": "DocPlane",
        "migrations": migration_count,
        "pages": page_count,
        "certification": certification_status().get("state"),
        "build": {
            "git_sha": os.environ.get("GIT_SHA", "unknown"),
            "built_at": os.environ.get("BUILD_TIMESTAMP", "unknown"),
        },
    }
