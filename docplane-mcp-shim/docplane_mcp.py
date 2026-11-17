#!/usr/bin/env python3
"""
docplane-mcp — a read-only MCP shim over the DocPlane REST API.

Scope is structural, not configured: this server implements seven read
operations out of DocPlane's 104 (five page reads plus two typed WORK reads,
work_brief and work_history, which may read every initiative but write nothing). The dangerous verbs are absent because they
are not written here, not because a token was scoped correctly. The self-issued
DocPlane token is in fact read-write (role CONTRIBUTOR); this file is the only
boundary. Keep it that way.

Deliberately NOT implemented, and not to be added without an explicit decision:
  bootstrap/*        principal + token lifecycle (break-glass custody)
  model/*            architecture ownership model mutations
  changes/*          multi-operation change flow incl. publish
  reorganisation/*   bulk page moves / reparenting
  publication/retry  corpus republication
  pages/*/replace, /rollback, /verify, /classification, /owner
  maintenance/*, observations, observe/*, events/batch, workspaces POST,
  verification-requests/*

DocPlane's REST API covers documentation and WORK only; it has no
infrastructure or shell operations. This adapter therefore wraps that API
directly instead of any broader agent tool surface a deployment may run.

Transport: MCP stdio — newline-delimited JSON-RPC 2.0.
stdout carries protocol frames ONLY. Diagnostics go to stderr and the audit log.

Dependencies: none. Python standard library only, by design (PEP-668 safe).
"""

import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

SERVER_NAME = "docplane-mcp"
SERVER_VERSION = "1.1.0"
PROTOCOL_VERSION = "2025-06-18"

HERE = os.path.dirname(os.path.abspath(__file__))


def _env(name, default):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


# Required: there is deliberately no default deployment URL.
BASE_URL = _env("DOCPLANE_BASE_URL", "").rstrip("/")
AUDIT_LOG = _env("DOCPLANE_AUDIT_LOG", os.path.join(HERE, "audit", "docplane-mcp.log"))
MAX_RESPONSE_BYTES = int(_env("DOCPLANE_MAX_RESPONSE_BYTES", "40960"))
AUDIT_MAX_BYTES = int(_env("DOCPLANE_AUDIT_MAX_BYTES", str(5 * 1024 * 1024)))
HTTP_TIMEOUT = int(_env("DOCPLANE_HTTP_TIMEOUT", "60"))


def _default_ca_bundle():
    """An explicit PEM bundle for a private CA, or None for system trust."""
    return os.environ.get("DOCPLANE_CA_BUNDLE") or None


CA_BUNDLE = _default_ca_bundle()


def log_stderr(msg):
    sys.stderr.write("[%s] %s\n" % (SERVER_NAME, msg))
    sys.stderr.flush()


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------

def _rotate_if_needed(path):
    try:
        if os.path.exists(path) and os.path.getsize(path) > AUDIT_MAX_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass


def audit(tool, args, status, nbytes, principal_id=None, revision=None, note=None):
    """Append one audit line. Never raises — auditing must not break the tool."""
    try:
        d = os.path.dirname(AUDIT_LOG)
        if d:
            os.makedirs(d, mode=0o700, exist_ok=True)
        _rotate_if_needed(AUDIT_LOG)
        rec = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "tool": tool,
            "args": args,
            "http_status": status,
            "bytes": nbytes,
            "principal_id": principal_id,
            "revision": revision,
        }
        if note:
            rec["note"] = note
        with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        try:
            os.chmod(AUDIT_LOG, 0o600)
        except OSError:
            pass
    except Exception as exc:  # pragma: no cover - audit must never propagate
        log_stderr("audit write failed: %r" % (exc,))


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

class DocPlaneError(Exception):
    """A tool-visible error carrying an HTTP status when there is one."""

    def __init__(self, message, status=None, retry_after=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retry_after = retry_after


# --------------------------------------------------------------------------
# HTTP + auth
# --------------------------------------------------------------------------

class DocPlaneClient:
    def __init__(self):
        self._token = None
        self._principal_id = None
        self._expires_at = None
        if CA_BUNDLE:
            self._ctx = ssl.create_default_context(cafile=CA_BUNDLE)
        else:
            # No CA bundle configured: use system trust. Python does not read
            # the macOS keychain, so a private-CA deployment must set
            # DOCPLANE_CA_BUNDLE; say so rather than fail obscurely.
            self._ctx = ssl.create_default_context()
            log_stderr(
                "No DOCPLANE_CA_BUNDLE set; using system trust. "
                "Set it to a PEM path if DocPlane uses a private CA."
            )

    @property
    def principal_id(self):
        return self._principal_id

    def _display_name(self):
        try:
            host = socket.gethostname().split(".")[0]
        except Exception:
            host = "unknown-host"
        return "docplane-mcp-shim@%s" % host

    def _raw(self, method, path, body=None, token=None, extra_headers=None):
        if not BASE_URL:
            raise DocPlaneError("DOCPLANE_BASE_URL is not set; it names the DocPlane "
                                "deployment (its trusted front) to read from.")
        url = BASE_URL + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "%s/%s" % (SERVER_NAME, SERVER_VERSION))
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        for k, v in (extra_headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, context=self._ctx, timeout=HTTP_TIMEOUT) as resp:
                raw = resp.read()
                return resp.status, raw, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return exc.code, raw, dict(exc.headers or {})
        except urllib.error.URLError as exc:
            raise DocPlaneError(
                "Cannot reach DocPlane at %s (%s). Is DOCPLANE_BASE_URL reachable "
                "from this host, and is DOCPLANE_CA_BUNDLE correct? "
                "Underlying error: %r" % (BASE_URL, type(exc).__name__, exc.reason)
            )
        except socket.timeout:
            raise DocPlaneError("DocPlane request timed out after %ds: %s" % (HTTP_TIMEOUT, path))

    def _self_issue(self):
        """Mint a token. One principal per process — never written to disk."""
        status, raw, headers = self._raw(
            "POST",
            "/api/v1/auth/self-issue",
            {
                "display_name": self._display_name(),
                "client_context": "docplane-mcp read-only shim",
            },
        )
        if status == 429:
            ra = headers.get("Retry-After")
            raise DocPlaneError(
                "DocPlane rate limited credential issuance. Retry in %s seconds. "
                "(This ceiling applies to token issuance, not to reads.)"
                % (ra or "unknown"),
                status=429,
                retry_after=ra,
            )
        if status != 201:
            raise DocPlaneError(
                "Self-issue failed with HTTP %s. Self-service tokens exist only on "
                "deployments whose access profile enables them, through the trusted "
                "front named by DOCPLANE_BASE_URL." % status,
                status=status,
            )
        body = json.loads(raw)
        self._token = body["token"]
        self._principal_id = body.get("principal_id")
        self._expires_at = body.get("expires_at")
        log_stderr(
            "self-issued principal %s (role=%s, expires=%s)"
            % (self._principal_id, body.get("role"), self._expires_at)
        )
        return self._token

    def _ensure_token(self):
        if self._token is None:
            self._self_issue()
        return self._token

    def request(self, method, path, body=None):
        """
        Authenticated request.

        401 -> re-issue exactly once, then fail loudly. 429 is never a reason to
        re-issue; the two paths must not interact.
        """
        token = self._ensure_token()
        status, raw, headers = self._raw(method, path, body=body, token=token)

        if status == 429:
            ra = headers.get("Retry-After")
            raise DocPlaneError(
                "DocPlane rate limited this request. Retry in %s seconds. Do not "
                "retry immediately." % (ra or "unknown"),
                status=429,
                retry_after=ra,
            )

        if status == 401:
            log_stderr("401 received; re-issuing token once")
            self._token = None
            self._principal_id = None
            token = self._ensure_token()
            status, raw, headers = self._raw(method, path, body=body, token=token)
            if status == 401:
                raise DocPlaneError(
                    "DocPlane returned 401 twice, with a freshly issued token. "
                    "This is not a token-expiry problem — check that this "
                    "principal is active and that the request is well-formed.",
                    status=401,
                )
            if status == 429:
                ra = headers.get("Retry-After")
                raise DocPlaneError(
                    "DocPlane rate limited after token refresh. Retry in %s seconds."
                    % (ra or "unknown"),
                    status=429,
                    retry_after=ra,
                )

        if status >= 400:
            detail = ""
            try:
                parsed = json.loads(raw)
                detail = parsed.get("detail") or parsed.get("error") or ""
                if not isinstance(detail, str):
                    detail = json.dumps(detail)[:400]
            except Exception:
                detail = raw[:300].decode("utf-8", "replace")
            raise DocPlaneError(
                "DocPlane HTTP %s on %s — %s" % (status, path, detail), status=status
            )

        try:
            return status, json.loads(raw), len(raw)
        except Exception:
            raise DocPlaneError("DocPlane returned a non-JSON body on %s" % path)


CLIENT = DocPlaneClient()


# --------------------------------------------------------------------------
# Path resolution
# --------------------------------------------------------------------------

# Every caller- or server-supplied value that becomes a URL PATH segment must pass
# one of these validators first. Query parameters go through urlencode only.
_CANONICAL_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
# DocPlane heading IDs: explicit IDs are [A-Za-z0-9_-]+, generated slugs are a subset.
_HEADING_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")


def _canonical_uuid(s):
    """Lowercase canonical UUID text, or None. Rejects braces, URNs and bare hex."""
    if not isinstance(s, str) or not _CANONICAL_UUID_RE.match(s):
        return None
    return str(uuid.UUID(s))


def _page_rid(rec):
    """The page resource_id as a path segment; refuse anything not a canonical UUID."""
    rid = _canonical_uuid(str((rec or {}).get("resource_id") or ""))
    if rid is None:
        raise DocPlaneError("DocPlane returned a page without a canonical resource_id; refusing to use it.")
    return rid


def resolve_page(path):
    """
    Resolve a page path to its record via /api/v1/resolve.

    DocPlane's canonical paths carry a '.md' suffix and resolve is exact: a
    path without it returns count 0. Retry once with '.md' appended, but only
    when the given path has no extension at all — so a genuine typo still fails
    rather than being silently rewritten. The matched form is reported back.
    """
    rid = _canonical_uuid(path)
    if rid is not None:
        status, page, n = CLIENT.request("GET", "/api/v1/pages/%s?view=summary" % rid)
        return page, "resource_id", n

    attempts = [path]
    stem = os.path.basename(path)
    if "." not in stem:
        attempts.append(path + ".md")

    last_n = 0
    for attempt in attempts:
        q = urllib.parse.urlencode({"path": attempt})
        status, body, n = CLIENT.request("GET", "/api/v1/resolve?" + q)
        last_n = n
        matches = body.get("matches") or []
        if body.get("count"):
            if len(matches) > 1:
                names = ", ".join(m.get("path", "?") for m in matches[:5])
                raise DocPlaneError(
                    "Path '%s' is ambiguous — %d pages matched: %s"
                    % (attempt, len(matches), names)
                )
            return matches[0], attempt, n

    tried = " and ".join(repr(a) for a in attempts)
    raise DocPlaneError(
        "No DocPlane page resolved for %s. DocPlane paths are exact and normally "
        "end in '.md' (e.g. 'guides/overview.md'). Use the 'search' tool to "
        "find the canonical path." % tried
    )


def _meta(page, matched_form=None):
    m = {
        "path": page.get("path"),
        "title": page.get("title"),
        "resource_id": page.get("resource_id"),
        "revision": page.get("revision"),
        "version": page.get("version"),
        "updated_at": page.get("updated_at"),
        "updated_by": page.get("updated_by"),
    }
    if matched_form is not None:
        m["matched_form"] = matched_form
    return m


def _size(obj):
    return len(json.dumps(obj).encode("utf-8"))


# --------------------------------------------------------------------------
# Tools — reads only
# --------------------------------------------------------------------------

def tool_docplane_search(args):
    q = args.get("query")
    if not q:
        raise DocPlaneError("search requires 'query'.")
    params = {
        "q": q,
        "limit": int(args.get("limit", 20)),
        "include_archived": str(bool(args.get("include_archived", False))).lower(),
    }
    status, body, n = CLIENT.request("GET", "/api/v1/search?" + urllib.parse.urlencode(params))
    # NOTE: /api/v1/search does not return updated_at / updated_by (verified
    # against the live corpus). Emitting them as null would look like "never
    # updated", so they are omitted and the gap is stated in the payload.
    results = [
        {
            "path": r.get("path"),
            "title": r.get("title"),
            "nav_path": r.get("nav_path"),
            "resource_id": r.get("resource_id"),
            "revision": r.get("revision"),
            "version": r.get("version"),
            "workspace_key": r.get("workspace_key"),
            "matched_in": r.get("matched_in"),
            "summary": r.get("summary"),
            "snippet": r.get("snippet"),
        }
        for r in (body.get("results") or [])
    ]
    return {
        "query": body.get("query", q),
        "count": body.get("count"),
        "total": body.get("total"),
        "freshness_note": (
            "DocPlane's search endpoint returns revision and version but not "
            "updated_at/updated_by. For timestamps, call 'read', "
            "'outline' or 'list' on a specific path."
        ),
        "results": results,
    }, status, n, None


def _prefix_suggestions(paths, wanted, k=8):
    """Nearest real prefixes, to turn a zero-match into something actionable."""
    depths = set()
    for p in paths:
        segs = p.split("/")
        for d in (1, 2):
            if len(segs) > d:
                depths.add("/".join(segs[:d]) + "/")
    head = wanted.strip("/").split("/")[0]
    # Exact, then case-insensitive, then a shared-stem fallback. The
    # case-insensitive pass matters because a wrong-case prefix is the most
    # likely real mistake and must surface the correctly-cased directory.
    near = sorted(x for x in depths if x.startswith(head))
    if not near:
        near = sorted(x for x in depths if x.lower().startswith(head.lower()))
    if not near and len(head) >= 3:
        near = sorted(x for x in depths if x.lower().startswith(head[:3].lower()))
    return (near or sorted(depths))[:k]


def tool_docplane_list(args):
    """
    List pages.

    NOTE ON path_prefix: DocPlane's own `path` parameter on GET /api/v1/pages is an
    EXACT match, not a prefix — `path=guides/` returns 0 while
    `path=guides/security/access.md` returns 1. There is no glob or
    wildcard support ('%' and '*' both return 0), and no other parameter offers
    prefix semantics. So prefix filtering is done CLIENT-SIDE over the full page
    list, which is safe here: it is exact string matching on data the API already
    returned, with no ranking or relevance to misrepresent.

    A corpus under 2000 pages fits in one call (`limit` maxes at 2000)
    — so this costs one request, not a page-walk.
    """
    prefix = (args.get("path_prefix") or "").strip()
    status = args.get("status", "active")
    workspace_key = args.get("workspace_key")
    limit = int(args.get("limit", 100))

    params = {"status": status, "limit": 2000 if prefix else max(limit, 1)}
    if workspace_key:
        params["workspace_key"] = workspace_key

    http_status, body, n = CLIENT.request(
        "GET", "/api/v1/pages?" + urllib.parse.urlencode(params)
    )
    fetched = body.get("pages") or []
    total = body.get("total")

    def shape(p):
        return {
            "path": p.get("path"),
            "title": p.get("title"),
            "nav_path": p.get("nav_path"),
            "resource_id": p.get("resource_id"),
            "revision": p.get("revision"),
            "version": p.get("version"),
            "updated_at": p.get("updated_at"),
            "updated_by": p.get("updated_by"),
            "status": p.get("status"),
            "workspace_key": p.get("workspace_key"),
            "provenance": p.get("provenance"),
        }

    if not prefix:
        return {
            "count": body.get("count"),
            "total": total,
            "filter": {"status": status, "workspace_key": workspace_key},
            "pages": [shape(p) for p in fetched],
        }, http_status, n, None

    wanted = prefix.lstrip("/")
    matched = [p for p in fetched if (p.get("path") or "").startswith(wanted)]

    # Never return a silent empty set: a filter that matched nothing over a
    # non-empty corpus is a reportable condition, not an empty answer.
    if not matched and fetched:
        sugg = _prefix_suggestions([p.get("path") or "" for p in fetched], wanted)
        raise DocPlaneError(
            "path_prefix %r matched 0 of %d pages considered (corpus total %s, "
            "status=%s%s). The corpus is NOT empty — this filter selected nothing.\n"
            "Prefixes are matched client-side as exact leading strings against paths "
            "like 'guides/security/access.md', so they are "
            "case-sensitive and must start at the corpus root (no leading '/').\n"
            "Existing prefixes near yours: %s"
            % (
                prefix,
                len(fetched),
                total,
                status,
                ", workspace_key=%s" % workspace_key if workspace_key else "",
                ", ".join(sugg) or "(none)",
            )
        )

    truncated = len(matched) > limit
    out = matched[:limit]
    payload = {
        "count": len(out),
        "matched": len(matched),
        "considered": len(fetched),
        "total": total,
        "filter": {
            "path_prefix": prefix,
            "status": status,
            "workspace_key": workspace_key,
            "path_prefix_applied": "client-side (DocPlane's `path` is exact-match only)",
        },
        "pages": [shape(p) for p in out],
    }
    if truncated:
        payload["note"] = (
            "%d pages matched; showing the first %d. Raise `limit` or narrow "
            "`path_prefix`." % (len(matched), limit)
        )
    if total is not None and len(fetched) < total:
        payload["warning"] = (
            "Only %d of %d pages were retrieved, so this prefix filter did not see "
            "the whole corpus." % (len(fetched), total)
        )
    return payload, http_status, n, None


def tool_docplane_outline(args):
    path = args.get("path")
    if not path:
        raise DocPlaneError("outline requires 'path'.")
    rec, matched, _ = resolve_page(path)
    rid = _page_rid(rec)
    status, page, n = CLIENT.request("GET", "/api/v1/pages/%s?view=outline" % rid)
    out = dict(_meta(page, matched))
    out["outline"] = page.get("outline") or []
    out["heading_count"] = len(out["outline"])
    return out, status, n, page.get("revision")


def tool_docplane_section(args):
    path = args.get("path")
    heading_id = args.get("heading_id")
    if not path or not heading_id:
        raise DocPlaneError("section requires 'path' and 'heading_id'.")
    if not isinstance(heading_id, str) or not _HEADING_ID_RE.match(heading_id):
        raise DocPlaneError(
            "heading_id must be a DocPlane heading ID (letters, digits, '_' and '-'). "
            "Use a heading_id value returned by 'outline'.")
    rec, matched, _ = resolve_page(path)
    rid = _page_rid(rec)
    try:
        status, sec, n = CLIENT.request(
            "GET",
            "/api/v1/pages/%s/sections/%s" % (rid, urllib.parse.quote(heading_id, safe="")),
        )
    except DocPlaneError as exc:
        if exc.status in (404, 422):
            raise DocPlaneError(
                "Heading '%s' not found on %s. Call 'outline' first and use "
                "one of its heading_id values — headings change between revisions."
                % (heading_id, rec.get("path"))
            )
        raise
    out = dict(_meta(rec, matched))
    out.update(
        {
            "heading_id": sec.get("heading_id"),
            "section_title": sec.get("title"),
            "level": sec.get("level"),
            "start_line": sec.get("start_line"),
            "end_line": sec.get("end_line"),
            "content_hash": sec.get("content_hash"),
            "revision": sec.get("revision"),
            "version": sec.get("version"),
            "content": sec.get("content"),
        }
    )
    return out, status, n, sec.get("revision")


def tool_docplane_read(args):
    """
    Full page content, with a three-rung oversize ladder:
      full -> outline -> summary + top-level headings
    A page big enough to blow the budget is exactly the page whose outline may
    also blow it, so the last rung matters.

    COST NOTE: the ladder is decided *after* fetching view=full, so an oversize
    page is fetched in full and then discarded in favour of its outline. This is
    deliberate and currently unavoidable: view=summary (936 B) carries no
    content length, line count or byte-size field, so there is nothing cheap to
    branch on. Verified against the live API 2026-09-16. If DocPlane ever adds a
    size field to the summary view, restructure to summary -> decide -> fetch.
    """
    path = args.get("path")
    if not path:
        raise DocPlaneError("read requires 'path'.")
    rec, matched, _ = resolve_page(path)
    rid = _page_rid(rec)

    status, page, n = CLIENT.request("GET", "/api/v1/pages/%s?view=full" % rid)
    rev = page.get("revision")

    full = dict(_meta(page, matched))
    full["content"] = page.get("content") or ""
    full["truncated"] = False
    full["view_returned"] = "full"
    if _size(full) <= MAX_RESPONSE_BYTES:
        return full, status, n, rev

    # Rung 2 — outline
    status2, opage, n2 = CLIENT.request("GET", "/api/v1/pages/%s?view=outline" % rid)
    outline = opage.get("outline") or []
    rung2 = dict(_meta(opage, matched))
    rung2.update(
        {
            "view_returned": "outline",
            "truncated": True,
            "content_chars": len(page.get("content") or ""),
            "outline": outline,
            "heading_count": len(outline),
            "note": (
                "Page body is %d characters, over the %d-byte response budget. "
                "Returning the outline instead. Fetch what you need with "
                "section(path, heading_id)."
                % (len(page.get("content") or ""), MAX_RESPONSE_BYTES)
            ),
        }
    )
    if _size(rung2) <= MAX_RESPONSE_BYTES:
        return rung2, status2, n + n2, rev

    # Rung 3 — summary + top-level headings only
    tops = [h for h in outline if (h.get("level") or 99) <= 2]
    rung3 = dict(_meta(opage, matched))
    rung3.update(
        {
            "view_returned": "summary+top_headings",
            "truncated": True,
            "content_chars": len(page.get("content") or ""),
            "summary": opage.get("summary"),
            "heading_count": len(outline),
            "top_headings": [
                {"heading_id": h.get("heading_id"), "title": h.get("title"), "level": h.get("level")}
                for h in tops
            ],
            "note": (
                "Page body (%d chars) and its full outline (%d headings) both exceed "
                "the %d-byte budget. Returning top-level headings only. Use "
                "the 'outline' tool for the complete heading list, then "
                "'section' for content."
                % (len(page.get("content") or ""), len(outline), MAX_RESPONSE_BYTES)
            ),
        }
    )
    return rung3, status2, n + n2, rev


# --------------------------------------------------------------------------
# WORK reads (typed, read-only). Two fixed GET routes, nothing else.
#
# BOUNDARY. These tools widen what the shim can READ to all WORK initiatives;
# they add no write. The backend credential is the same self-issued,
# write-capable contributor token used by the page tools, so the read-only
# property is structural: only the GET routes below are implemented, every
# path is built from a validated UUID, and no caller-supplied path, URL,
# method or header is ever forwarded. Bodies come only from DocPlane's own
# redacted projections; withheld fields stay withheld.
# --------------------------------------------------------------------------

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,95}$")
HISTORY_DEFAULT_LIMIT = 10
HISTORY_MAX_LIMIT = 50


def _initiative_id(ref):
    """Resolve a canonical UUID or an exact initiative_key; never a fuzzy match."""
    if not isinstance(ref, str) or not ref.strip():
        raise DocPlaneError("'initiative' is required: a canonical UUID or an exact initiative_key.")
    ref = ref.strip()
    if _UUID_RE.match(ref):
        return ref, None
    if not _KEY_RE.match(ref):
        raise DocPlaneError("'initiative' must be a lowercase canonical UUID or an exact initiative_key.")
    status, body, n = CLIENT.request(
        "GET", "/api/v1/initiatives?" + urllib.parse.urlencode(
            {"key": ref, "include_closed": "true", "view": "summary", "limit": 5}))
    rows = [r for r in (body.get("initiatives") or []) if r.get("initiative_key") == ref]
    if len(rows) != 1 or not _UUID_RE.match(str(rows[0].get("initiative_id", ""))):
        raise DocPlaneError("No initiative has the exact key %r." % ref, status=404)
    return rows[0]["initiative_id"], ref


def tool_work_brief(args):
    """Current brief: objective, named items, next actions, holds, recent evidence."""
    iid, key = _initiative_id(args.get("initiative"))
    status, brief, n = CLIENT.request("GET", "/api/v1/initiatives/%s/brief" % iid)
    if brief.get("contract") != "work-brief-v1":
        raise DocPlaneError("Unexpected brief contract %r; refusing to project it."
                            % brief.get("contract"))
    # The context token binds WRITES to this read. This connection has no
    # write tool, so it is removed rather than offered.
    brief.pop("context_token", None)
    out = dict(brief)
    out["projection"] = {"tool": "work_brief", "read_only": True, "context_token_removed": True,
                         "resolved_from_key": key, "truncated": False}
    if _size(out) > MAX_RESPONSE_BYTES:
        recent = out.get("recent_activity") or []
        out["recent_activity"] = [
            {k: a.get(k) for k in ("activity_id", "activity_type", "created_at", "author_principal_id")}
            for a in recent]
        out["projection"].update({
            "truncated": True,
            "note": "Brief exceeded the %d-byte budget; recent_activity bodies were dropped. "
                    "Read them with work_history." % MAX_RESPONSE_BYTES})
    if _size(out) > MAX_RESPONSE_BYTES:
        raise DocPlaneError("Brief exceeds the response budget even without activity bodies.")
    return out, status, n, (out.get("initiative") or {}).get("version")


def tool_work_history(args):
    """Redacted activity history, oldest first, explicitly paginated."""
    iid, key = _initiative_id(args.get("initiative"))
    try:
        offset = int(args.get("offset", 0))
        limit = int(args.get("limit", HISTORY_DEFAULT_LIMIT))
    except (TypeError, ValueError):
        raise DocPlaneError("'offset' and 'limit' must be integers.")
    if offset < 0 or not 1 <= limit <= HISTORY_MAX_LIMIT:
        raise DocPlaneError("'offset' must be >= 0 and 'limit' between 1 and %d." % HISTORY_MAX_LIMIT)
    status, value, n = CLIENT.request(
        "GET", "/api/v1/initiatives/%s?activity_bodies=redacted" % iid)
    # Fail closed against a server that ignored the parameter and returned raw bodies.
    if value.get("activity_bodies") != "redacted" or value.get("activity_metadata") != "allowlisted-v1":
        raise DocPlaneError("DocPlane did not confirm the redacted projection; refusing to return history.")
    activities = value.get("activities") or []
    total = len(activities)
    page = activities[offset:offset + limit]
    out = {
        "initiative": {k: value.get(k) for k in (
            "initiative_id", "initiative_key", "title", "work_state", "priority", "version",
            "updated_at", "owner_principal_id")},
        "owner_principal_kind": value.get("owner_principal_kind"),
        "links": value.get("links") or [],
        "dependencies": value.get("dependencies") or [],
        "activities": page,
        "pagination": {"order": "oldest_first", "offset": offset, "requested_limit": limit,
                       "returned": len(page), "total": total},
        "projection": {"tool": "work_history", "read_only": True, "activity_bodies": "redacted",
                       "resolved_from_key": key},
    }
    # Shrink the page, never a body, until it fits; say so.
    while _size(out) > MAX_RESPONSE_BYTES and len(out["activities"]) > 1:
        out["activities"] = out["activities"][:-1]
    returned = len(out["activities"])
    if _size(out) > MAX_RESPONSE_BYTES:
        raise DocPlaneError("A single activity exceeds the response budget; it cannot be returned whole.")
    nxt = offset + returned
    out["pagination"].update({
        "returned": returned,
        "budget_truncated": returned < len(page),
        "next_offset": nxt if nxt < total else None,
        "has_more": nxt < total,
    })
    return out, status, n, value.get("version")


TOOLS = [
    {
        "name": "search",
        "description": (
            "Full-text search across DocPlane documentation. Returns "
            "canonical page paths, titles and current revisions. Use this first "
            "when you do not already know a page's exact path.\n\n"
            "Matching is AND: every distinct term must appear somewhere in a "
            "page's title, path, nav_path or content. Adding a term always "
            "narrows. Common words like 'access' or 'model' match broadly, so a "
            "high 'total' is a match count, NOT a relevance count — results are "
            "ranked best-first and the answer is usually in the top few.\n\n"
            "There is no phrase quoting and no field-scoped syntax: \"a b\", "
            "a AND b and title:a are all just terms (title:a is read as two "
            "terms, 'title' and 'a'). There is NO stopword removal: filler words are "
            "kept and matched as substrings, so 'the' hits almost every page and "
            "'is' hits 647. They barely narrow the result set but they DO feed the "
            "relevance sum, which changes what ranks first. Prefer a few distinctive terms: "
            "'backup retention policy' ranks the right page first where "
            "'what is the backup retention policy' does not. Max 12 terms; "
            "minimum 2 characters."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms."},
                "limit": {"type": "integer", "default": 20},
                "include_archived": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        },
        "fn": tool_docplane_search,
    },
    {
        "name": "list",
        "description": (
            "Browse DocPlane pages. Good for surveying a section of the corpus when "
            "you want coverage rather than relevance — 'search' ranks, 'list' "
            "enumerates.\n\n"
            "Each page returns path, title, nav_path, revision, version, updated_at, "
            "updated_by plus three fields worth filtering on:\n"
            "  status       — active (default) | archived | all.\n"
            "  workspace_key— classification, NOT a path. For example 'reference' "
            "or 'operations'. A workspace spans directories, so "
            "one workspace_key can include pages under "
            "several top-level paths. Useful as a browse axis, but it is not a path filter.\n"
            "  provenance   — AUTHORED (human-written) or GENERATED (produced by a "
            "generator; do not plan edits against these without checking the "
            "generating contract). Returned on every page; not a filter.\n\n"
            "path_prefix matches the start of a page path, e.g. "
            "'guides/security/'. It is applied CLIENT-SIDE: DocPlane's own "
            "`path` parameter is exact-match, so no server-side prefix filter exists. "
            "Matching is case-sensitive, rooted (no leading '/'), and costs one "
            "request for the whole corpus. If it matches nothing you get an explicit "
            "error naming nearby real prefixes — never a silent empty list."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path_prefix": {
                    "type": "string",
                    "description": "Leading path segment, e.g. 'guides/security/'. Client-side, case-sensitive.",
                },
                "workspace_key": {
                    "type": "string",
                    "description": "Classification key, e.g. 'reference' or 'operations'. Server-side, exact.",
                },
                "status": {"type": "string", "default": "active", "enum": ["active", "archived", "all"]},
                "limit": {"type": "integer", "default": 100, "description": "Max pages returned after filtering."},
            },
        },
        "fn": tool_docplane_list,
    },
    {
        "name": "read",
        "description": (
            "Read a DocPlane page's full Markdown by path (paths normally end in "
            "'.md'). Large pages degrade to an outline, then to top-level headings, "
            "rather than truncating silently. Always returns revision and updated_at."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Canonical path, e.g. 'guides/overview.md', or a resource_id UUID.",
                }
            },
            "required": ["path"],
        },
        "fn": tool_docplane_read,
    },
    {
        "name": "outline",
        "description": (
            "Fetch a page's heading structure without its body — heading_id, title, "
            "level and line bounds. Use on large pages before 'section'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "fn": tool_docplane_outline,
    },
    {
        "name": "section",
        "description": (
            "Fetch one heading-bounded section of a page. Get heading_id values from the "
            "'outline' tool first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "heading_id": {"type": "string", "description": "From the 'outline' tool."},
            },
            "required": ["path", "heading_id"],
        },
        "fn": tool_docplane_section,
    },
    {
        "name": "work_brief",
        "description": (
            "Read the current DocPlane WORK brief for one initiative (by canonical UUID or exact "
            "initiative_key): objective, named items with state and stance, next actions, holds, "
            "deferrals, recorded completion and recent evidence. Read-only; recorded evidence is "
            "not live verification. No write is possible through this connection."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "initiative": {"type": "string",
                               "description": "Canonical lowercase UUID or exact initiative_key."}
            },
            "required": ["initiative"],
        },
        "fn": tool_work_brief,
    },
    {
        "name": "work_history",
        "description": (
            "Read one initiative's WORK activity history through DocPlane's redacted projection, "
            "oldest first, plus its links and cross-initiative dependencies. Explicitly paginated: "
            "check pagination.has_more and pass pagination.next_offset as 'offset'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "initiative": {"type": "string",
                               "description": "Canonical lowercase UUID or exact initiative_key."},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": ["initiative"],
        },
        "fn": tool_work_history,
    },
]

def _typed(tool):
    """Refuse arguments whose JSON type differs from the tool's declared schema."""
    props = tool["inputSchema"].get("properties", {})
    fn = tool["fn"]

    def checked(args):
        if not isinstance(args, dict):
            raise DocPlaneError("arguments must be an object.")
        for key, value in args.items():
            kind = (props.get(key) or {}).get("type")
            if value is None or kind is None:
                continue
            ok = (isinstance(value, str) if kind == "string" else
                  isinstance(value, int) and not isinstance(value, bool) if kind == "integer" else True)
            if not ok:
                raise DocPlaneError("argument %r must be of type %s." % (key, kind))
        return fn(args)
    checked.__name__ = getattr(fn, "__name__", "tool")
    return checked


for _tool in TOOLS:
    _tool["fn"] = _typed(_tool)

TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


# --------------------------------------------------------------------------
# JSON-RPC / MCP stdio
# --------------------------------------------------------------------------

def send(msg):
    """The ONLY writer to stdout."""
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def reply(req_id, result):
    send({"jsonrpc": "2.0", "id": req_id, "result": result})


def reply_error(req_id, code, message):
    send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def handle_tools_call(req_id, params):
    name = (params or {}).get("name")
    args = (params or {}).get("arguments") or {}
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        reply_error(req_id, -32602, "Unknown tool: %r" % name)
        audit(str(name), args, None, 0, CLIENT.principal_id, None, note="unknown tool")
        return

    started = time.time()
    try:
        payload, status, nbytes, revision = tool["fn"](args)
        text = json.dumps(payload, indent=2, sort_keys=False)
        audit(name, args, status, nbytes, CLIENT.principal_id, revision)
        reply(req_id, {"content": [{"type": "text", "text": text}], "isError": False})
    except DocPlaneError as exc:
        audit(name, args, exc.status, 0, CLIENT.principal_id, None, note=exc.message[:300])
        reply(
            req_id,
            {"content": [{"type": "text", "text": "DocPlane error: " + exc.message}], "isError": True},
        )
    except Exception as exc:  # unexpected — still must not kill the server
        log_stderr("unhandled error in %s: %r" % (name, exc))
        audit(name, args, None, 0, CLIENT.principal_id, None, note="unhandled: %r" % (exc,))
        reply(
            req_id,
            {
                "content": [{"type": "text", "text": "Unexpected %s in %s: %s" % (type(exc).__name__, name, exc)}],
                "isError": True,
            },
        )
    finally:
        log_stderr("%s took %.2fs" % (name, time.time() - started))


def handle(msg):
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        client_pv = params.get("protocolVersion") or PROTOCOL_VERSION
        reply(
            req_id,
            {
                "protocolVersion": client_pv,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
        return

    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return  # notifications take no response

    if method == "ping":
        reply(req_id, {})
        return

    if method == "tools/list":
        reply(
            req_id,
            {"tools": [{k: t[k] for k in ("name", "description", "inputSchema")} for t in TOOLS]},
        )
        return

    if method == "tools/call":
        handle_tools_call(req_id, params)
        return

    if method in ("resources/list", "prompts/list"):
        reply(req_id, {"resources": []} if method == "resources/list" else {"prompts": []})
        return

    if req_id is not None:
        reply_error(req_id, -32601, "Method not found: %s" % method)


def main():
    log_stderr(
        "starting; base=%s ca=%s audit=%s max_bytes=%d"
        % (BASE_URL, CA_BUNDLE, AUDIT_LOG, MAX_RESPONSE_BYTES)
    )
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            log_stderr("dropped non-JSON line")
            continue
        try:
            handle(msg)
        except Exception as exc:  # never die on one bad message
            log_stderr("fatal-ish error handling %r: %r" % (msg.get("method"), exc))
            if msg.get("id") is not None:
                reply_error(msg["id"], -32603, "Internal error: %s" % exc)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
