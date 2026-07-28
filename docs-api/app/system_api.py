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
        cur.execute(
            """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE status = 'active') AS active,
                   count(*) FILTER (WHERE status = 'archived') AS archived
              FROM docs.pages
            """
        )
        total, active, archived = (int(value) for value in cur.fetchone())
    return {
        "status": "ok",
        "product": "DocPlane",
        "migrations": migration_count,
        # Keep the original field for compatibility, but make its unit explicit.
        "pages": total,
        "page_counts": {
            "total": total,
            "active": active,
            "archived": archived,
        },
        "certification": certification_status().get("state"),
        "build": {
            "git_sha": os.environ.get("GIT_SHA", "unknown"),
            "built_at": os.environ.get("BUILD_TIMESTAMP", "unknown"),
        },
    }
