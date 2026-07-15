"""[Docs] tools — search, read, write documentation via the Docs API."""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import httpx
import yaml
from lunr import lunr

from common import DOCS_API_URL, pick

INDEX_PATH = Path(os.environ.get(
    "DOCS_MCP_INDEX_PATH",
    "/data/mkdocs/site/search/search_index.json",
))
DOCS_DIR = Path(os.environ.get(
    "DOCS_MCP_DOCS_DIR",
    "/data/mkdocs/docs",
))
ALIASES_PATH = Path(os.environ.get(
    "DOCS_MCP_ALIASES_PATH",
    "/app/aliases.yml",
))
REFRESH_INTERVAL_SEC = int(os.environ.get("DOCS_MCP_REFRESH_INTERVAL", "300"))

_lock = threading.Lock()
_index = None
_docs_by_ref: dict[str, dict] = {}
_path_lookup: dict[str, str] = {}
_basename_lookup: dict[str, str] = {}
_index_mtime: float = 0.0
_aliases: dict[str, dict] = {}
_aliases_mtime: float = 0.0
_docs_api_key: str = ""
_key_lock = threading.Lock()


def _loc_to_md(location: str) -> str:
    loc = location.split("#", 1)[0].rstrip("/")
    return "index.md" if not loc else f"{loc}.md"


def _build_index() -> None:
    global _index, _docs_by_ref, _path_lookup, _basename_lookup, _index_mtime
    st = INDEX_PATH.stat()
    if st.st_mtime == _index_mtime and _index is not None:
        return
    raw = json.loads(INDEX_PATH.read_text())
    docs = raw.get("docs", [])
    lunr_docs, new_by_ref, new_path, new_base = [], {}, {}, {}
    for i, d in enumerate(docs):
        ref = str(i)
        entry = {"ref": ref, "location": d.get("location", ""),
                 "title": d.get("title", ""), "text": d.get("text", "")}
        lunr_docs.append(entry)
        new_by_ref[ref] = entry
        mp = _loc_to_md(entry["location"])
        if mp not in new_path:
            new_path[mp] = ref
            bn = mp.rsplit("/", 1)[-1].removesuffix(".md")
            if bn and bn not in new_base:
                new_base[bn] = mp
    new_idx = lunr(
        ref="ref",
        fields=[{"field_name": "title", "boost": 10}, {"field_name": "text", "boost": 1}],
        documents=lunr_docs,
    )
    with _lock:
        _index = new_idx
        _docs_by_ref = new_by_ref
        _path_lookup = new_path
        _basename_lookup = new_base
        _index_mtime = st.st_mtime
    print(f"[mcp/docs] index built: {len(lunr_docs)} entries, {len(new_path)} pages",
          file=sys.stderr)


def _load_aliases() -> dict:
    global _aliases, _aliases_mtime
    if not ALIASES_PATH.exists():
        return _aliases
    try:
        st = ALIASES_PATH.stat()
        if st.st_mtime == _aliases_mtime and _aliases:
            return _aliases
        data = yaml.safe_load(ALIASES_PATH.read_text()) or {}
        normalized = {k.strip().lower(): v for k, v in (data.get("aliases") or {}).items()}
        with _lock:
            _aliases = normalized
            _aliases_mtime = st.st_mtime
        return normalized
    except Exception as e:
        print(f"[mcp/docs] aliases load failed: {e}", file=sys.stderr)
        return _aliases


def _refresher() -> None:
    while True:
        time.sleep(REFRESH_INTERVAL_SEC)
        try:
            _build_index()
        except Exception as e:
            print(f"[mcp/docs] refresh failed: {e}", file=sys.stderr)
        try:
            _load_aliases()
        except Exception as e:
            print(f"[mcp/docs] aliases refresh failed: {e}", file=sys.stderr)


def _bootstrap_key() -> str:
    global _docs_api_key
    with _key_lock:
        if _docs_api_key:
            return _docs_api_key
    # Take the key from env injection (compose sets DOCS_API_KEY on this container).
    # The /api/docs/* endpoints this MCP calls have always required X-API-Key;
    # /api/agent-config remains an anonymous fabric bootstrap by design, but a
    # long-lived service should not depend on a runtime HTTP bootstrap for its
    # credential — prefer the pre-provisioned env key. Do not bootstrap over HTTP.
    key = os.environ.get("DOCS_API_KEY", "")
    if key:
        with _key_lock:
            _docs_api_key = key
        return key
    print(
        "[mcp/docs] DOCS_API_KEY not set in env; docs-api calls will be "
        "unauthenticated and will fail (set DOCS_API_KEY on this container)",
        file=sys.stderr,
    )
    return ""


def _docs_api(method: str, path: str, *, body: dict | None = None,
              if_match: str | None = None) -> tuple[int, dict | list]:
    key = _bootstrap_key()
    if not key:
        return 503, {"error": "docs-api key unavailable"}
    headers: dict[str, str] = {"X-API-Key": key}
    if if_match:
        headers["If-Match"] = if_match
    content = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        content = json.dumps(body).encode()
    try:
        r = httpx.request(method, f"{DOCS_API_URL}{path}", headers=headers,
                          content=content, timeout=15)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"raw": r.text}
    except Exception as e:
        return 503, {"error": f"docs-api unreachable: {e}"}


def _snippet(text: str, query: str, length: int = 200) -> str:
    if not text:
        return ""
    tokens = [t for t in re.findall(r"\w+", query.lower()) if t]
    lower = text.lower()
    for tok in tokens:
        i = lower.find(tok)
        if i >= 0:
            start = max(0, i - 80)
            end = min(len(text), i + 120)
            return f"{'...' if start else ''}{text[start:end]}{'...' if end < len(text) else ''}"
    return text[:length]


def init() -> None:
    # Never let a missing/corrupt search index crash the whole server at boot
    # (e.g. a fresh install whose site volume hasn't had its first deploy yet) —
    # the refresher thread and the /reindex webhook will pick it up once built.
    try:
        _build_index()
    except FileNotFoundError:
        print(f"[mcp/docs] search index not found at {INDEX_PATH} — "
              "serving without it until the first deploy/reindex", file=sys.stderr)
    except Exception as e:
        print(f"[mcp/docs] initial index build failed: {e}", file=sys.stderr)
    _load_aliases()
    _bootstrap_key()
    threading.Thread(target=_refresher, daemon=True).start()


def register(mcp) -> None:

    @mcp.tool()
    def search_docs(query: str, top_k: int = 10) -> list[dict]:
        """[Docs] Keyword search over the documentation site, ranked via Lunr.

        CALL THIS FIRST before WebFetch or filesystem Read/Grep when the user
        asks about this project's infrastructure, services, runbooks, or any
        internal acronym.

        Returns [{path, title, snippet, score}] ranked best-first, deduplicated by page."""
        with _lock:
            if _index is None:
                return [{"error": "index not yet loaded"}]
            idx, docs = _index, _docs_by_ref
        try:
            results = idx.search(query)
        except Exception as e:
            return [{"error": f"search failed: {e}"}]
        seen: set[str] = set()
        out: list[dict] = []
        for r in results[: top_k * 3]:
            entry = docs.get(r["ref"])
            if not entry:
                continue
            mp = _loc_to_md(entry["location"])
            if mp in seen:
                continue
            seen.add(mp)
            out.append({"path": mp, "title": entry["title"],
                        "snippet": _snippet(entry["text"], query),
                        "score": round(r["score"], 4)})
            if len(out) >= top_k:
                break
        return out

    @mcp.tool()
    def read_doc(path_or_slug: str) -> dict:
        """[Docs] Fetch full source markdown of a docs page.

        Accepts canonical path ('agent-guides/start-here.md'), bare basename
        ('start-here'), or slug. Use AFTER search_docs returns a promising hit.

        Returns {path, title, content_markdown, last_updated} on success
        or {error, query, suggestions} when no page matches."""
        with _lock:
            path_lookup = dict(_path_lookup)
            basename_lookup = dict(_basename_lookup)
            docs = _docs_by_ref
        md_path: str | None = None
        if path_or_slug in path_lookup:
            md_path = path_or_slug
        elif (path_or_slug + ".md") in path_lookup:
            md_path = path_or_slug + ".md"
        else:
            bn = path_or_slug.removesuffix(".md").rsplit("/", 1)[-1]
            md_path = basename_lookup.get(bn)
            if md_path is None:
                for p in path_lookup:
                    if p.endswith("/" + bn + ".md"):
                        md_path = p
                        break
        if md_path is None:
            bn = path_or_slug.removesuffix(".md").rsplit("/", 1)[-1]
            return {"error": "not found", "query": path_or_slug,
                    "suggestions": [p for p in path_lookup if bn and bn in p][:3]}
        abs_path = DOCS_DIR / md_path
        ref = path_lookup.get(md_path)
        entry = docs.get(ref) if ref else None
        title = entry["title"] if entry else md_path
        try:
            content = abs_path.read_text()
            st = abs_path.stat()
            return {"path": md_path, "title": title, "content_markdown": content,
                    "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime))}
        except FileNotFoundError:
            return {"path": md_path, "title": title,
                    "content_markdown": entry["text"] if entry else "",
                    "last_updated": None, "note": "source markdown not on disk"}

    @mcp.tool()
    def resolve_concept(term: str) -> dict:
        """[Docs] Translate project jargon/aliases to canonical concepts.

        Use FIRST when the user mentions an unfamiliar internal term or
        project nickname. Returns {term, canonical, summary, docs[]} on match
        or {} when no alias is registered."""
        aliases = _load_aliases()
        if not aliases:
            return {}
        t = term.strip().lower()
        entry = aliases.get(t)
        if entry is None:
            for k, v in aliases.items():
                if t in k or k in t:
                    entry = v
                    break
        if entry is None:
            return {}
        return {"term": term, "canonical": entry.get("canonical", ""),
                "summary": entry.get("summary", ""), "docs": entry.get("docs", [])}

    @mcp.tool()
    def list_docs(status: str = "active") -> list[dict]:
        """[Docs] List all documentation pages.

        status — 'active' (default), 'inactive', 'archived', 'all'.
        Returns [{path, title, nav_path, version, updated_at, updated_by}] sorted by path."""
        code, body = _docs_api("GET", f"/api/docs/pages?status={status}")
        if code != 200 or not isinstance(body, list):
            return [{"error": f"docs-api returned {code}"}]
        return [{"path": p.get("path", ""), "title": p.get("title", ""),
                 "nav_path": p.get("nav_path", ""), "version": p.get("version"),
                 "updated_at": p.get("updated_at", ""), "updated_by": p.get("updated_by", "")}
                for p in body]

    @mcp.tool()
    def write_doc(
        path: str,
        title: str,
        nav_path: str,
        content: str,
        updated_by: str = "claude-code",
    ) -> dict:
        """[Docs] Create or update a documentation page.

        path      — canonical path e.g. 'services/my-service.md'
        title     — display title shown in the nav
        nav_path  — nav tree location e.g. 'Services/My Service'
        content   — full markdown (must start with '# Heading')
        updated_by — audit label (default: 'claude-code')

        Returns {path, version, action, revision}. Deploy is automatic (~10s)."""
        get_code, get_body = _docs_api("GET", f"/api/docs/pages/{path}")
        if get_code == 200:
            revision, action = get_body.get("revision"), "updated"
        elif get_code == 404:
            revision, action = None, "created"
        else:
            return {"error": f"GET failed with status {get_code}", "detail": get_body}
        put_code, put_body = _docs_api(
            "PUT", f"/api/docs/pages/{path}",
            body={"title": title, "nav_path": nav_path,
                  "content": content, "updated_by": updated_by},
            if_match=revision,
        )
        if put_code in (200, 201):
            return {"path": put_body.get("path", path), "version": put_body.get("version"),
                    "action": action, "revision": put_body.get("revision")}
        if put_code == 412:
            return {"error": "conflict",
                    "detail": "page modified concurrently — call write_doc again to retry"}
        if put_code == 422:
            return {"error": "validation_error", "detail": put_body.get("detail", put_body)}
        return {"error": f"unexpected PUT status {put_code}", "detail": put_body}

    @mcp.tool()
    def archive_doc(path: str) -> dict:
        """[Docs] Soft-delete a documentation page (recoverable).

        Removes from the live site but keeps full version history in the database.
        Returns {path, action: 'archived'} on success."""
        get_code, get_body = _docs_api("GET", f"/api/docs/pages/{path}")
        if get_code == 404:
            return {"error": "not_found", "detail": f"no active page at {path!r}"}
        if get_code != 200:
            return {"error": f"GET failed with status {get_code}", "detail": get_body}
        revision = get_body.get("revision")
        arc_code, arc_body = _docs_api("POST", f"/api/docs/pages/{path}/archive",
                                       if_match=revision)
        if arc_code == 200:
            return {"path": path, "action": "archived"}
        if arc_code == 412:
            return {"error": "conflict", "detail": "modified concurrently — retry"}
        return {"error": f"unexpected archive status {arc_code}", "detail": arc_body}


# ---------------------------------------------------------------------------
# /reindex — webhook target from docs-api after a deploy completes
# ---------------------------------------------------------------------------
# Waits up to REINDEX_MAX_WAIT_SEC for INDEX_PATH's mtime to differ from
# the currently-indexed _index_mtime, then rebuilds. Runs in a daemon
# thread so the HTTP handler returns 202 immediately (fire-and-forget
# semantics for the caller).

REINDEX_MAX_WAIT_SEC = int(os.environ.get("DOCS_MCP_REINDEX_MAX_WAIT", "30"))
_reindex_lock = threading.Lock()


def _wait_and_reindex() -> str:
    """Block until INDEX_PATH differs from indexed mtime, then rebuild.
    Returns 'rebuilt', 'no-change', 'missing', or 'error'."""
    start = time.time()
    while True:
        try:
            st = INDEX_PATH.stat()
        except FileNotFoundError:
            if time.time() - start > REINDEX_MAX_WAIT_SEC:
                return "missing"
            time.sleep(0.5)
            continue
        if st.st_mtime != _index_mtime:
            try:
                _build_index()
                return "rebuilt"
            except Exception as e:
                print(f"[mcp/docs] reindex build failed: {e}", file=sys.stderr)
                return "error"
        if time.time() - start > REINDEX_MAX_WAIT_SEC:
            return "no-change"
        time.sleep(0.5)


async def _reindex_route(request):
    from starlette.responses import JSONResponse
    if not _reindex_lock.acquire(blocking=False):
        return JSONResponse({"status": "in_progress"}, status_code=202)

    def _bg():
        try:
            outcome = _wait_and_reindex()
            print(f"[mcp/docs] reindex bg outcome: {outcome}", file=sys.stderr)
        except Exception as e:
            print(f"[mcp/docs] reindex bg crash: {e}", file=sys.stderr)
        finally:
            _reindex_lock.release()

    threading.Thread(target=_bg, daemon=True, name="reindex-bg").start()
    return JSONResponse(
        {"status": "accepted", "max_wait_sec": REINDEX_MAX_WAIT_SEC},
        status_code=202,
    )


def register_routes(app) -> None:
    """Attach the /reindex route to a Starlette/ASGI app. Called from server.py."""
    from starlette.routing import Route
    app.router.routes.append(Route("/reindex", _reindex_route, methods=["POST"]))
