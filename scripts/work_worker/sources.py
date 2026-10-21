"""Governed, read-only access to WORK records.

Two interchangeable sources return results shaped like the consolidated MCP's
WORK read tools: ``McpReads`` (allowlisted read verbs over a caller-supplied
call function) and ``SnapshotReads`` (a preserved or synthetic fixture). The
worker never calls the contributor API, SQL, or any write verb.
"""
from __future__ import annotations

import json
import os
import stat

READ_VERBS = frozenset({
    "docplane_work_get", "docplane_work_capture_get",
    "docplane_work_captures", "docplane_work_initiatives",
})
PAGE = 100            # activities per docplane_work_get call (tool maximum)
MAX_PAGES = 200       # hard stop; a longer history is reported incomplete


class ReadFailure(Exception):
    """A read failed, was refused, or could not be completed.

    Carries only a short code, never upstream text.
    """

    def __init__(self, code):
        super().__init__(code)
        self.code = str(code)[:64]


def _checked(data):
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    if not isinstance(data, dict):
        raise ReadFailure("MALFORMED_RESULT")
    if "error" in data:
        code = data.get("code") or "READ_ERROR"
        raise ReadFailure(code if isinstance(code, str) else "READ_ERROR")
    return data


class Reads:
    """Common traversal logic over a ``fetch(tool, args) -> dict`` primitive."""

    def fetch(self, tool, args):
        raise NotImplementedError

    def describe(self):
        raise NotImplementedError

    def capture(self, capture_id):
        return _checked(self.fetch("docplane_work_capture_get", {"capture_id": capture_id}))

    def initiative(self, initiative_id):
        """Return the initiative with every activity page, or raise ReadFailure.

        A history that cannot be followed to its full ``activity_count`` is a failure,
        never a shorter list: absence of evidence cannot be claimed from a partial read.
        """
        offset, activities, head, seen = 0, [], None, set()
        for _ in range(MAX_PAGES):
            page = _checked(self.fetch("docplane_work_get", {
                "initiative_id": initiative_id, "recent_activity_limit": PAGE,
                "activity_offset": offset}))
            if page.get("initiative_id") != initiative_id:
                raise ReadFailure("IDENTITY_MISMATCH")
            if not isinstance(page.get("activity_count"), int) or isinstance(page.get("activity_count"), bool) or page["activity_count"] < 0:
                raise ReadFailure("ACTIVITY_COUNT_MISSING")
            if head is None:
                head = page
            elif page.get("version") != head.get("version"):
                raise ReadFailure("VERSION_CHANGED")
            elif page["activity_count"] != head["activity_count"]:
                raise ReadFailure("COUNT_CHANGED")
            if "activity_offset" in page and page["activity_offset"] != offset:
                raise ReadFailure("OFFSET_MISMATCH")
            got = page.get("activities")
            if not isinstance(got, list):
                raise ReadFailure("MALFORMED_RESULT")
            for act in got:
                aid = act.get("activity_id") if isinstance(act, dict) else None
                if not isinstance(aid, str) or aid in seen:
                    raise ReadFailure("DUPLICATE_ACTIVITY" if aid in seen else "MALFORMED_RESULT")
                seen.add(aid)
            activities.extend(got)
            nxt = page.get("next_activity_offset")
            if nxt is None:
                # The live server leaves activities_truncated true on the final page of a
                # paged history; the cursor's absence ends the walk and the count check below
                # decides whether the history is complete.
                break
            if not isinstance(nxt, int) or nxt <= offset or nxt != offset + len(got):
                raise ReadFailure("CURSOR_NOT_ADVANCING")
            offset = nxt
        else:
            raise ReadFailure("TOO_MANY_PAGES")
        if head["activity_count"] != len(activities):
            raise ReadFailure("ACTIVITY_COUNT_MISMATCH")
        return {**head, "activities": activities}


class McpReads(Reads):
    """Read verbs of the consolidated CharlieHub MCP through ``call(tool, args)``."""

    def __init__(self, call, label="live-mcp"):
        self._call, self._label = call, label

    def fetch(self, tool, args):
        if tool not in READ_VERBS:
            raise PermissionError("not a read verb: " + tool)
        try:
            return self._call(tool, args)
        except (PermissionError, ReadFailure):
            raise
        except Exception as exc:        # transport/protocol failure: no upstream text kept
            raise ReadFailure(type(exc).__name__) from None

    def describe(self):
        return {"mode": self._label, "live": True}


def load_api_key(path, variable="CHARLIEHUB_MCP_API_KEY"):
    """Read the MCP key from a 0600 env-style or raw file. Parsed, never sourced or printed."""
    if os.stat(path).st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError("credential file must not be group/world accessible")
    with open(path) as handle:
        lines = [x.strip() for x in handle if x.strip() and not x.lstrip().startswith("#")]
    for line in lines:
        name, sep, value = line.partition("=")
        if sep and name.removeprefix("export ").strip() == variable:
            return value.strip().strip("\"'")
    if len(lines) == 1 and "=" not in lines[0]:
        return lines[0]
    raise ValueError("credential file does not define " + variable)


def _is_loopback(host):
    import ipaddress
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:                          # a name such as 127.0.0.1.example.com is not an address
        return False


class _NoRedirect:
    """urllib handler that turns every redirect into an error instead of following it (the bearer must go nowhere else)."""

    def __new__(cls):
        import urllib.request

        class Handler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        return Handler()


class McpHttpClient:
    """Minimal streamable-HTTP MCP client (stdlib only) that can call read verbs only.

    The allowlist is enforced here, before any network traffic, as well as in
    ``McpReads``. ``requests`` logs method and verb names only, for acceptance evidence.
    """

    def __init__(self, url, api_key, cafile=None, timeout=60, allow_loopback_http=False):
        import ssl
        import urllib.parse
        import urllib.request
        parts = urllib.parse.urlsplit(url)
        loopback = _is_loopback(parts.hostname)
        if parts.scheme != "https" and not (parts.scheme == "http" and allow_loopback_http and loopback):
            raise ValueError("the MCP url must be https (plain http only to a loopback test endpoint that is explicitly permitted)")
        self._url, self._timeout = url, timeout
        self._key = api_key
        self._ctx = ssl.create_default_context(cafile=cafile)
        # A redirect would replay the Authorization header to wherever the response points: refuse every redirect.
        self._opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=self._ctx), _NoRedirect())
        self._session, self._id = None, 0
        self.requests = []

    def _post(self, payload):
        import urllib.error
        import urllib.request
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "Authorization": "Bearer " + self._key}
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        req = urllib.request.Request(self._url, json.dumps(payload).encode(), headers, method="POST")
        try:
            resp = self._opener.open(req, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            exc.close()
            raise ReadFailure("REDIRECT_REFUSED" if 300 <= exc.code < 400 else "HTTP_%d" % exc.code) from None
        with resp:
            self._session = resp.headers.get("Mcp-Session-Id") or self._session
            body = resp.read().decode()
            kind = resp.headers.get("Content-Type", "")
        if "text/event-stream" in kind:
            events = [x[5:].strip() for x in body.splitlines() if x.startswith("data:")]
            body = events[-1] if events else ""
        return json.loads(body) if body.strip() else None

    def _rpc(self, method, params, notify=False):
        self.requests.append(method + (":" + params["name"] if method == "tools/call" else ""))
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notify:
            self._id += 1
            payload["id"] = self._id
        reply = self._post(payload)
        if notify:
            return None
        if not isinstance(reply, dict) or "error" in reply:
            raise ReadFailure("RPC_ERROR")
        return reply.get("result")

    def open(self):
        self._rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                 "clientInfo": {"name": "work-worker", "version": "0"}})
        self._rpc("notifications/initialized", {}, notify=True)
        return self

    def __call__(self, tool, args):
        if tool not in READ_VERBS:
            raise PermissionError("not a read verb: " + tool)
        if self._session is None and self._id == 0:
            self.open()
        res = self._rpc("tools/call", {"name": tool, "arguments": args})
        if res.get("isError"):
            raise ReadFailure("TOOL_ERROR")
        if res.get("structuredContent") is not None:
            return res["structuredContent"]
        return json.loads(res["content"][0]["text"])


# Allowlisted projection kept by RecordingReads: identity, state and structure only.
_CAPTURE_KEEP = ("capture_id", "created_at", "kind", "author_principal_id", "body_withheld", "origin_text_withheld", "status")
_HEAD_KEEP = ("initiative_id", "initiative_key", "work_state", "version", "updated_at", "activity_count", "activity_offset",
              "activities_truncated", "next_activity_offset", "owner", "founding_capture_ids")
_ACT_KEEP = ("activity_id", "activity_type", "created_at", "author_principal_id", "source_capture_id")


class RecordingReads(Reads):
    """Wrap a source and keep a text-free snapshot of exactly what the worker read."""

    def __init__(self, inner):
        self._inner = inner
        self.snapshot = {"as_of": None, "captures": {}, "initiatives": {}, "failures": {}}

    def fetch(self, tool, args):
        key = args.get("capture_id") or args.get("initiative_id")
        try:
            data = self._inner.fetch(tool, args)
        except ReadFailure as exc:
            self.snapshot["failures"][key] = exc.code
            raise
        res = data["result"] if isinstance(data, dict) and set(data) == {"result"} else data
        if isinstance(res, dict) and "error" in res:
            self.snapshot["failures"][key] = str(res.get("code") or "READ_ERROR")[:64]
        elif tool == "docplane_work_capture_get":
            self.snapshot["captures"][key] = {k: res.get(k) for k in _CAPTURE_KEEP}
        elif tool == "docplane_work_get":
            page = {k: res[k] for k in _HEAD_KEEP if k in res}
            page["activities"] = [{**{k: a.get(k) for k in _ACT_KEEP}, "references": [
                {"reference_type": r.get("reference_type"), "reference_id": r.get("reference_id"),
                 "reference_id_withheld": r.get("reference_id_withheld", False)} for r in a.get("references") or []]}
                for a in res.get("activities") or []]
            self.snapshot["initiatives"].setdefault(key, []).append(page)
        return data

    def describe(self):
        return self._inner.describe()


class SnapshotReads(Reads):
    """Replay of preserved or synthetic results, in the MCP tool result shape.

    ``snapshot`` = {"as_of": str, "captures": {id: result}, "initiatives":
    {id: [page, ...]}, "failures": {id: code}}. A failure entry simulates a
    refused or failed read of that record.
    """

    def __init__(self, snapshot):
        self._s = snapshot

    def fetch(self, tool, args):
        if tool not in READ_VERBS:
            raise PermissionError("not a read verb: " + tool)
        if tool == "docplane_work_capture_get":
            key = args["capture_id"]
            if key in self._s.get("failures", {}):
                return {"error": "x", "code": self._s["failures"][key]}
            if key not in self._s["captures"]:
                return {"error": "x", "code": "CAPTURE_NOT_FOUND"}
            return self._s["captures"][key]
        if tool == "docplane_work_get":
            key = args["initiative_id"]
            if key in self._s.get("failures", {}):
                return {"error": "x", "code": self._s["failures"][key]}
            pages = self._s["initiatives"].get(key)
            if not pages:
                return {"error": "x", "code": "INITIATIVE_NOT_FOUND"}
            index = 0
            offset = args.get("activity_offset", 0)
            for i, page in enumerate(pages):
                if page.get("activity_offset", 0) == offset:
                    index = i
                    break
            return pages[index]
        raise ReadFailure("UNSUPPORTED_IN_SNAPSHOT")

    def describe(self):
        return {"mode": "snapshot", "live": False, "as_of": self._s.get("as_of", "unknown")}
