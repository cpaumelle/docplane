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
    def work_capture(text: str, kind: str = "IDEA", context: str = "", idempotency_key: str = "") -> dict:
        """Park an out-of-scope discovery in the work inbox and RETURN to your task.

        This is the distraction ledger. When you notice something mid-task that
        is not the task — a bug (kind=BUG), a possible enhancement
        (kind=IMPROVEMENT), an idea, a question — record it in this ONE call
        and continue what you were doing. Do not investigate it, do not fix it,
        do not widen your scope: the capture exists precisely so the thought is
        safe to walk away from. Triage is a separate, deliberate act later.

        Zero-decision: pass the thought as one string. Pass context
        (repository, file, what you were doing) so triage knows where it came
        from. Pass your own idempotency_key to make a retry after a lost
        response replay the original receipt.
        """
        if kind not in {"IDEA", "NEXT_ACTION", "FINDING", "QUESTION", "BUG", "IMPROVEMENT"}:
            return {"error": "kind must be IDEA, NEXT_ACTION, FINDING, QUESTION, BUG or IMPROVEMENT"}
        origin = {"channel": "MCP", "server": MCP_SERVER_NAME, "tool": "work_capture"}
        if context.strip():
            origin["context"] = context.strip()[:4000]
        code, body = _request("POST", "/api/v1/work/captures", body={"body": text, "kind": kind, "origin": origin}, idempotency_key=idempotency_key.strip() or _key("capture"))
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
    def work_transition(
        initiative_id: str,
        to_state: str,
        expected_version: int,
        note: str = "",
        blocker_summary: str = "",
        parked_reason: str = "",
        parked_indefinitely: bool = False,
        soak_started_at: str = "",
        soak_review_at: str = "",
        soak_success_criteria: str = "",
        soak_failure_conditions: str = "",
        soak_monitoring_ref: str = "",
        model_disposition: str = "",
        model_disposition_note: str = "",
        observe_disposition: str = "",
        observe_disposition_note: str = "",
        idempotency_key: str = "",
    ) -> dict:
        """Move an initiative through its state machine with optimistic concurrency.

        BLOCKED requires blocker_summary; PARKED requires parked_reason and
        parked_indefinitely=true (or set a review date through the API);
        SOAKING requires the four soak_* fields (ISO datetimes) plus
        observability (soak_monitoring_ref or a typed evidence link);
        COMPLETE requires know promotion (see work_promote) and model/observe
        dispositions (UPDATED with evidence links, or NOT_REQUIRED/DEFERRED
        with a note). Pass your own idempotency_key for safe replay.
        """
        payload: dict = {"work_state": to_state, "expected_version": expected_version}
        for field, value in (
            ("note", note), ("blocker_summary", blocker_summary), ("parked_reason", parked_reason),
            ("soak_started_at", soak_started_at), ("soak_review_at", soak_review_at),
            ("soak_success_criteria", soak_success_criteria), ("soak_failure_conditions", soak_failure_conditions),
            ("soak_monitoring_ref", soak_monitoring_ref),
            ("model_disposition", model_disposition), ("model_disposition_note", model_disposition_note),
            ("observe_disposition", observe_disposition), ("observe_disposition_note", observe_disposition_note),
        ):
            if value.strip():
                payload[field] = value.strip()
        if parked_indefinitely:
            payload["parked_indefinitely"] = True
        code, body = _request("POST", f"/api/v1/initiatives/{initiative_id}/transition", body=payload, idempotency_key=idempotency_key.strip() or _key("transition"))
        return body if code == 200 else _error(code, body)

    @mcp.tool()
    def work_promote(initiative_id: str, promotion_state: str, expected_version: int, promotion_change_id: str = "") -> dict:
        """Record the know-domain closure disposition: PROMOTED (requires the
        publishing change id), NOT_REQUIRED, READY, IN_PROGRESS or NOT_READY."""
        payload: dict = {"promotion_state": promotion_state, "expected_version": expected_version}
        if promotion_change_id.strip():
            payload["promotion_change_id"] = promotion_change_id.strip()
        code, body = _request("PUT", f"/api/v1/initiatives/{initiative_id}/promotion", body=payload)
        return body if code == 200 else _error(code, body)

    @mcp.tool()
    def work_dispositions(initiative_id: str, expected_version: int, model_disposition: str = "", model_note: str = "", observe_disposition: str = "", observe_note: str = "", soak_monitoring_ref: str = "") -> dict:
        """Record model/observe closure dispositions ahead of completion.
        UPDATED requires a typed evidence link on the initiative; NOT_REQUIRED
        and DEFERRED require a note (DEFERRED mints a visible inbox gap)."""
        payload: dict = {"expected_version": expected_version}
        for field, value in (
            ("model_disposition", model_disposition), ("model_disposition_note", model_note),
            ("observe_disposition", observe_disposition), ("observe_disposition_note", observe_note),
            ("soak_monitoring_ref", soak_monitoring_ref),
        ):
            if value.strip():
                payload[field] = value.strip()
        code, body = _request("PUT", f"/api/v1/initiatives/{initiative_id}/dispositions", body=payload)
        return body if code == 200 else _error(code, body)

    @mcp.tool()
    def work_link(initiative_id: str, relation: str, resource_type: str, resource_id: str) -> dict:
        """Attach a typed, existence-checked link to an initiative — the
        evidence closure dispositions resolve against (e.g. UPDATES a
        MODEL_ENTITY, PRODUCES an ARTIFACT, EVIDENCE an OBSERVATION)."""
        code, body = _request("POST", f"/api/v1/initiatives/{initiative_id}/links", body={"relation": relation, "resource_type": resource_type, "resource_id": resource_id})
        return body if code == 201 else _error(code, body)
