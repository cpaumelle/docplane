"""DocPlane work-domain MCP tools: GTD capture, triage and initiative flow.

Thin clients of the same contributor API as the dashboard. Capture is
zero-decision by design — one sentence, no choices; triage is the deliberate
second act.
"""
from __future__ import annotations

from uuid import uuid4

import httpx

from common import DOCPLANE_API_URL, DOCPLANE_TOKEN, MCP_SERVER_NAME


def _key(prefix: str) -> str:
    return f"mcp-{prefix}-{uuid4()}"


def _request(method: str, path: str, *, body: dict | None = None, idempotency_key: str | None = None):
    if not DOCPLANE_TOKEN:
        return 503, {"error": "DOCPLANE_TOKEN is not configured"}
    headers = {"Authorization": f"Bearer {DOCPLANE_TOKEN}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        response = httpx.request(method, f"{DOCPLANE_API_URL}{path}", headers=headers, json=body, timeout=60)
        try:
            payload = response.json()
        except Exception:
            payload = {"raw": response.text}
        return response.status_code, payload
    except Exception as exc:
        return 503, {"error": f"DocPlane API unreachable: {exc}"}


def _error(code: int, body) -> dict:
    return {"error": f"DocPlane API returned {code}", "detail": body}


def register(mcp) -> None:
    @mcp.tool()
    def work_capture(text: str, kind: str = "IDEA", context: str = "") -> dict:
        """Save an idea, next action, finding or question to the work inbox without breaking flow.

        Zero-decision: pass the thought as one string. Optionally pass context
        (repository, session, what you were doing) so triage knows where the
        thought came from.
        """
        if kind not in {"IDEA", "NEXT_ACTION", "FINDING", "QUESTION"}:
            return {"error": "kind must be IDEA, NEXT_ACTION, FINDING or QUESTION"}
        origin = {"channel": "MCP", "server": MCP_SERVER_NAME, "tool": "work_capture"}
        if context.strip():
            origin["context"] = context.strip()[:4000]
        code, body = _request("POST", "/api/v1/work/captures", body={"body": text, "kind": kind, "origin": origin}, idempotency_key=_key("capture"))
        return body if code == 201 else _error(code, body)

    @mcp.tool()
    def work_inbox() -> dict:
        """List untriaged captures awaiting a triage decision, plus the work queues."""
        code, body = _request("GET", "/api/v1/work/captures?status=INBOX")
        if code != 200:
            return _error(code, body)
        queues_code, queues = _request("GET", "/api/v1/work/queues")
        return {"inbox": body, "queues": queues if queues_code == 200 else None}

    @mcp.tool()
    def work_triage(capture_id: str, action: str, initiative_id: str = "", title: str = "", note: str = "") -> dict:
        """Triage one inbox capture: promote (new BACKLOG initiative), attach (note on an existing initiative, pass initiative_id), or discard."""
        if action == "promote":
            payload: dict = {}
            if title.strip():
                payload["title"] = title.strip()
            if note.strip():
                payload["note"] = note.strip()
            code, body = _request("POST", f"/api/v1/work/captures/{capture_id}/promote", body=payload, idempotency_key=_key("triage"))
        elif action == "attach":
            if not initiative_id.strip():
                return {"error": "attach requires initiative_id"}
            payload = {"initiative_id": initiative_id.strip()}
            if note.strip():
                payload["note"] = note.strip()
            code, body = _request("POST", f"/api/v1/work/captures/{capture_id}/attach", body=payload, idempotency_key=_key("triage"))
        elif action == "discard":
            code, body = _request("POST", f"/api/v1/work/captures/{capture_id}/discard", body={"note": note.strip() or None}, idempotency_key=_key("triage"))
        else:
            return {"error": "action must be promote, attach or discard"}
        return body if code == 200 else _error(code, body)

    @mcp.tool()
    def work_list(state: str = "") -> dict:
        """List open initiatives, optionally filtered by state (BACKLOG, ACTIVE, BLOCKED, SOAKING, PAUSED, PARKED, COMPLETE, ABANDONED)."""
        params = httpx.QueryParams({"work_state": state}) if state.strip() else None
        path = f"/api/v1/initiatives?{params}" if params else "/api/v1/initiatives"
        code, body = _request("GET", path)
        return body if code == 200 else _error(code, body)

    @mcp.tool()
    def work_get(initiative_id: str) -> dict:
        """Read one initiative with its activities, links and dependencies."""
        code, body = _request("GET", f"/api/v1/initiatives/{initiative_id}")
        return body if code == 200 else _error(code, body)

    @mcp.tool()
    def work_note(initiative_id: str, text: str, activity_type: str = "NOTE") -> dict:
        """Append an activity to an initiative's timeline (NOTE, OBSERVATION, DECISION_REQUIRED, HANDOFF, SOAK_OBSERVATION, BLOCKER, RESOLUTION)."""
        code, body = _request("POST", f"/api/v1/initiatives/{initiative_id}/activities", body={"activity_type": activity_type, "body": text}, idempotency_key=_key("note"))
        return body if code == 201 else _error(code, body)

    @mcp.tool()
    def work_transition(initiative_id: str, to_state: str, expected_version: int, note: str = "", blocker_summary: str = "", parked_reason: str = "", parked_indefinitely: bool = False) -> dict:
        """Move an initiative through its state machine with optimistic concurrency.

        BLOCKED requires blocker_summary; PARKED requires parked_reason and
        parked_indefinitely=true (or set a review date through the API).
        SOAKING has a richer contract — use the API directly for soak entry.
        """
        payload: dict = {"work_state": to_state, "expected_version": expected_version}
        if note.strip():
            payload["note"] = note.strip()
        if blocker_summary.strip():
            payload["blocker_summary"] = blocker_summary.strip()
        if parked_reason.strip():
            payload["parked_reason"] = parked_reason.strip()
        if parked_indefinitely:
            payload["parked_indefinitely"] = True
        code, body = _request("POST", f"/api/v1/initiatives/{initiative_id}/transition", body=payload, idempotency_key=_key("transition"))
        return body if code == 200 else _error(code, body)
