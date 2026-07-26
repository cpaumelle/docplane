"""Best-effort first-party usage instrumentation for supported HTTP endpoints.

Instrumentation is observational: event-store failure is logged and never changes the response returned by
the authoritative product operation. Explicit batch ingestion remains available for richer web, MCP, AI
and CLI events.
"""
from __future__ import annotations

import hashlib
import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.agent_auth import authenticate
from app.db import get_conn
from app.event_store import append_event


log = logging.getLogger(__name__)


class UsageEventMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if response.status_code >= 400:
            return response
        observation = self._classify(request)
        if observation is None:
            return response
        try:
            principal = authenticate(request.headers.get("Authorization"))
        except Exception:
            principal = None
        try:
            request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
            with get_conn() as conn:
                append_event(
                    conn,
                    event_type=observation["event_type"],
                    channel="API",
                    producer_id="docplane-api-auto",
                    idempotency_key=request_id,
                    principal=principal,
                    client_identity=request.headers.get("User-Agent", "")[:200] or None,
                    resource_type=observation.get("resource_type"),
                    resource_id=observation.get("resource_id"),
                    page_resource_id=observation.get("page_resource_id"),
                    metadata=observation.get("metadata"),
                )
                conn.commit()
        except Exception as exc:
            log.warning("usage event was not recorded: %s", type(exc).__name__)
        return response

    @staticmethod
    def _classify(request: Request):
        path = request.url.path
        method = request.method.upper()
        if path.startswith("/api/v1/events") or path.startswith("/api/v1/analytics"):
            return None
        if method == "GET" and path.startswith("/api/v1/pages/"):
            resource_id = path.rsplit("/", 1)[-1]
            return {
                "event_type": "PAGE_READ_API",
                "resource_type": "PAGE",
                "resource_id": resource_id,
                "page_resource_id": resource_id,
                "metadata": {"view": request.query_params.get("view", "summary")},
            }
        if method == "GET" and path == "/api/v1/search":
            query = " ".join(request.query_params.get("q", "").lower().split())
            return {
                "event_type": "SEARCH_SUBMITTED",
                "resource_type": "SEARCH",
                "metadata": {
                    "query_fingerprint": hashlib.sha256(query.encode("utf-8")).hexdigest()
                    if query
                    else None,
                    "query_length": len(query),
                },
            }
        if method == "POST" and path == "/api/v1/resolve-canonical":
            return {"event_type": "CANONICAL_RESOLUTION_REQUESTED", "resource_type": "SEARCH"}
        if method == "POST" and path == "/api/v1/changes":
            return {"event_type": "CHANGE_PROPOSED", "resource_type": "CHANGE"}
        if method == "POST" and path.endswith("/validate") and "/api/v1/changes/" in path:
            return {
                "event_type": "CHANGE_VALIDATED",
                "resource_type": "CHANGE",
                "resource_id": path.split("/")[4],
            }
        if method == "POST" and path.endswith("/submit") and "/api/v1/changes/" in path:
            return {
                "event_type": "CHANGE_SUBMITTED",
                "resource_type": "CHANGE",
                "resource_id": path.split("/")[4],
            }
        return None
