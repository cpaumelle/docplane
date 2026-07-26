from __future__ import annotations

import hmac
import json
import os
import pathlib
import re
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Query


_SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_DATA_DIR = pathlib.Path(os.environ.get("DOCPLANE_CATALOG_DATA", "/var/lib/docplane/catalog"))
_API_KEY = os.environ.get("DOCPLANE_CATALOG_API_KEY", "")

app = FastAPI(
    title="DocPlane Schema Catalog",
    version="0.1.0",
    description="Machine-oriented, generated observations of registered database schemas.",
)


def _require_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    if not _API_KEY:
        raise HTTPException(status_code=503, detail="DOCPLANE_CATALOG_API_KEY is not configured")
    if not x_api_key or not hmac.compare_digest(x_api_key, _API_KEY):
        raise HTTPException(status_code=403, detail="invalid or missing X-API-Key")


def _source_dir(source: str) -> pathlib.Path:
    if not _SOURCE_RE.fullmatch(source):
        raise HTTPException(status_code=400, detail="invalid source name")
    return _DATA_DIR / source


def _read_json(path: pathlib.Path, *, missing_status: int = 404) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(status_code=missing_status, detail=f"catalog artifact not found: {path.name}")
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "CATALOG_ARTIFACT_UNREADABLE", "artifact": path.name, "type": type(exc).__name__},
        )
    if not isinstance(value, dict):
        raise HTTPException(status_code=503, detail=f"catalog artifact is not an object: {path.name}")
    return value


def _latest_paths(source: str) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    root = _source_dir(source)
    latest = _read_json(root / "latest.json")
    fingerprint = latest.get("fingerprint")
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise HTTPException(status_code=503, detail="latest.json carries an invalid fingerprint")
    snapshot = root / "snapshots" / fingerprint
    return root, snapshot / "manifest.json", snapshot / "schema.json"


@app.get("/healthz")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "data_dir": str(_DATA_DIR),
        "data_dir_present": _DATA_DIR.is_dir(),
    }


@app.get("/api/catalog/sources")
def list_sources(x_api_key: Optional[str] = Header(default=None)) -> list[dict[str, Any]]:
    _require_key(x_api_key)
    if not _DATA_DIR.exists():
        return []

    result: list[dict[str, Any]] = []
    for entry in sorted(_DATA_DIR.iterdir(), key=lambda path: path.name):
        if not entry.is_dir() or not _SOURCE_RE.fullmatch(entry.name):
            continue
        latest = _read_json(entry / "latest.json") if (entry / "latest.json").exists() else None
        status = _read_json(entry / "status.json") if (entry / "status.json").exists() else None
        result.append({"source": entry.name, "latest": latest, "status": status})
    return result


@app.get("/api/catalog/sources/{source}/latest")
def latest_source(source: str, x_api_key: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _require_key(x_api_key)
    root, manifest_path, _ = _latest_paths(source)
    return {
        "source": source,
        "status": _read_json(root / "status.json", missing_status=503),
        "manifest": _read_json(manifest_path, missing_status=503),
    }


@app.get("/api/catalog/sources/{source}/schema")
def source_schema(source: str, x_api_key: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _require_key(x_api_key)
    _, _, schema_path = _latest_paths(source)
    return _read_json(schema_path, missing_status=503)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _matches(query: str, *values: Any) -> bool:
    haystack = "\n".join(_text(value).lower() for value in values)
    return query in haystack


def _search_schema(source: str, schema: dict[str, Any], query: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    tables = schema.get("tables") or []
    if not isinstance(tables, list):
        return results

    for table in tables:
        if not isinstance(table, dict):
            continue
        table_name = _text(table.get("name"))
        table_comment = _text(table.get("comment") or table.get("desc"))
        if _matches(query, table_name, table_comment):
            results.append(
                {
                    "source": source,
                    "kind": "table",
                    "table": table_name,
                    "name": table_name,
                    "comment": table_comment or None,
                }
            )
        columns = table.get("columns") or []
        if not isinstance(columns, list):
            continue
        for column in columns:
            if not isinstance(column, dict):
                continue
            column_name = _text(column.get("name"))
            column_type = _text(column.get("type"))
            column_comment = _text(column.get("comment") or column.get("desc"))
            if _matches(query, table_name, column_name, column_type, column_comment):
                results.append(
                    {
                        "source": source,
                        "kind": "column",
                        "table": table_name,
                        "name": column_name,
                        "type": column_type or None,
                        "comment": column_comment or None,
                    }
                )
    return results


@app.get("/api/catalog/search")
def search_catalog(
    q: str = Query(min_length=2, max_length=128),
    source: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    x_api_key: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_key(x_api_key)
    query = q.strip().lower()
    if len(query) < 2:
        raise HTTPException(status_code=422, detail="q must contain at least two non-space characters")

    if source is not None:
        source_names = [source]
    elif _DATA_DIR.exists():
        source_names = [
            path.name
            for path in sorted(_DATA_DIR.iterdir(), key=lambda item: item.name)
            if path.is_dir() and _SOURCE_RE.fullmatch(path.name)
        ]
    else:
        source_names = []

    results: list[dict[str, Any]] = []
    for source_name in source_names:
        try:
            _, _, schema_path = _latest_paths(source_name)
            schema = _read_json(schema_path, missing_status=503)
        except HTTPException as exc:
            if source is not None:
                raise
            if exc.status_code in (404, 503):
                continue
            raise
        results.extend(_search_schema(source_name, schema, query))
        if len(results) >= limit:
            break

    return {"query": q, "count": min(len(results), limit), "results": results[:limit]}
