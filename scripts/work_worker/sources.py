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

        Truncation that cannot be followed to the end is a failure, never a
        shorter list: absence of evidence cannot be claimed from a partial read.
        """
        offset, activities, head = 0, [], None
        for _ in range(MAX_PAGES):
            page = _checked(self.fetch("docplane_work_get", {
                "initiative_id": initiative_id, "recent_activity_limit": PAGE,
                "activity_offset": offset}))
            head = head or page
            if page.get("initiative_id") != initiative_id:
                raise ReadFailure("IDENTITY_MISMATCH")
            got = page.get("activities")
            if not isinstance(got, list):
                raise ReadFailure("MALFORMED_RESULT")
            activities.extend(got)
            nxt = page.get("next_activity_offset")
            if nxt is None:
                if page.get("activities_truncated"):
                    raise ReadFailure("TRUNCATED_WITHOUT_CURSOR")
                break
            if not isinstance(nxt, int) or nxt <= offset:
                raise ReadFailure("CURSOR_NOT_ADVANCING")
            offset = nxt
        else:
            raise ReadFailure("TOO_MANY_PAGES")
        total = head.get("activity_count")
        if isinstance(total, int) and total != len(activities):
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


def open_mcp_session(url, bearer_file):
    """Return a synchronous ``call`` for the MCP read verbs (needs the ``mcp`` SDK).

    The bearer is read from a 0600 file, never from argv or the URL.
    """
    info = os.stat(bearer_file)
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError("bearer file must not be group/world accessible")
    with open(bearer_file) as handle:
        token = handle.read().strip()
    import asyncio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    def call(tool, args):
        async def go():
            async with streamablehttp_client(url, headers={"Authorization": "Bearer " + token}) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    res = await session.call_tool(tool, args)
                    if res.structuredContent is not None:
                        return res.structuredContent
                    return json.loads(res.content[0].text)
        return asyncio.run(go())
    return call


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
