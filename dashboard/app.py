from __future__ import annotations

import os
import pathlib
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import structure


STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"
app = FastAPI(
    title="DocPlane Operator Dashboard",
    version="0.1.0",
    description="Read-only corpus observability for operators.",
)


@contextmanager
def _connection():
    conn = psycopg2.connect(
        host=os.environ.get("DB_HOST", "postgres"),
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ.get("DB_NAME", "docs"),
        user=os.environ.get("DB_USER", "docs_dashboard"),
        password=os.environ.get("DB_PASS", ""),
        connect_timeout=int(os.environ.get("DB_CONNECT_TIMEOUT", "5")),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    conn.autocommit = False
    try:
        yield conn
    finally:
        conn.close()


def _load_pages() -> list[dict]:
    with _connection() as conn:
        cur = conn.cursor()
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cur.execute("SHOW transaction_isolation")
        isolation = str(cur.fetchone()["transaction_isolation"]).lower()
        if isolation not in ("repeatable read", "serializable"):
            raise RuntimeError(f"dashboard snapshot isolation is {isolation!r}")
        cur.execute(
            "SELECT path, title, nav_path, content, revision, version, status "
            "FROM docs.pages ORDER BY path"
        )
        pages = [dict(row) for row in cur.fetchall()]
        conn.rollback()
        return pages


@app.get("/healthz")
def health() -> dict:
    try:
        with _connection() as conn:
            cur = conn.cursor()
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SELECT 1 AS ok")
            ok = cur.fetchone()["ok"] == 1
            conn.rollback()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"status": "unavailable", "type": type(exc).__name__},
        )
    return {"status": "ok" if ok else "unavailable", "read_only": True}


@app.get("/api/structure")
def get_structure() -> dict:
    try:
        return structure.build(_load_pages())
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "STRUCTURE_SNAPSHOT_UNAVAILABLE", "type": type(exc).__name__},
        )


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")
