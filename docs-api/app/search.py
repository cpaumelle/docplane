import json
import logging
import os
import re
import threading
from datetime import datetime, timezone

from app.db import get_conn

log = logging.getLogger(__name__)

DOCS_CONTENT_DIR = os.environ.get("DOCS_CONTENT_DIR", "/docs-content")
INDEX_PATH = os.path.join(DOCS_CONTENT_DIR, ".search_index.json")

_SNIPPET_RADIUS = 150  # chars either side of first match
_INDEX_LOCK = threading.Lock()
_REBUILD_PENDING = threading.Event()


def build_index(pages: list[dict]) -> None:
    """Write search index from active pages. Called after every deploy.

    If a build is already running, sets a pending flag so the latest state
    is captured in a follow-up rebuild once the current one completes.
    """
    if not _INDEX_LOCK.acquire(blocking=False):
        _REBUILD_PENDING.set()
        log.info("search index build in progress — pending flag set")
        return
    try:
        _REBUILD_PENDING.clear()
        _write_index(pages)
        # If another caller arrived while we were building, do one more pass
        # with a fresh DB snapshot to guarantee eventual consistency.
        if _REBUILD_PENDING.is_set():
            _REBUILD_PENDING.clear()
            log.info("search index: pending rebuild — fetching fresh pages")
            fresh_pages = _fetch_active_pages()
            _write_index(fresh_pages)
    finally:
        _INDEX_LOCK.release()


def _fetch_active_pages() -> list[dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT path, nav_path, content, updated_at FROM docs.pages "
            "WHERE status = 'active' ORDER BY path"
        )
        rows = cur.fetchall()
    return [{"path": r[0], "nav_path": r[1], "content": r[2], "updated_at": r[3]}
            for r in rows]


def _write_index(pages: list[dict]) -> None:
    """Inner write — caller must hold _INDEX_LOCK."""
    git_sha = os.getenv("GIT_SHA", "unknown")
    entries = [
        {
            "path":     p["path"],
            "title":    _extract_title(p),
            "nav_path": p["nav_path"],
            "content":  p["content"],
        }
        for p in pages
    ]
    data = {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "git_sha":       git_sha,
        "pages_indexed": len(entries),
        "pages":         entries,
    }
    tmp = INDEX_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, INDEX_PATH)
    log.info("search index written: %d pages git_sha=%s → %s", len(entries), git_sha, INDEX_PATH)


def load_index() -> dict | None:
    try:
        with open(INDEX_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def search(q: str, limit: int = 20) -> list[dict]:
    """Case-insensitive substring search over title, nav_path, content. Ranked by relevance."""
    index = load_index()
    if index is None:
        return []

    q_lower = q.lower()
    scored = []
    for entry in index["pages"]:
        title_hit   = q_lower in entry["title"].lower()
        nav_hit     = q_lower in entry["nav_path"].lower()
        content_hit = q_lower in entry["content"].lower()
        if not (title_hit or nav_hit or content_hit):
            continue
        score = (5 if title_hit else 0) + (3 if nav_hit else 0) + (1 if content_hit else 0)
        scored.append((score, {
            "path":     entry["path"],
            "title":    entry["title"],
            "nav_path": entry["nav_path"],
            "snippet":  _snippet(entry["content"], q_lower) if content_hit else "",
        }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:limit]]


def index_status() -> dict:
    index = load_index()
    if index is None:
        return {"status": "not_built", "pages_indexed": 0, "generated_at": None, "git_sha": None}
    return {
        "status":        "ok",
        "pages_indexed": index["pages_indexed"],
        "generated_at":  index["generated_at"],
        "git_sha":       index.get("git_sha", "unknown"),
    }


def index_drift(db_active_count: int) -> dict:
    """Compare active page count in DB against index. Returns drift status."""
    index = load_index()
    if index is None:
        return {"status": "not_built", "db_count": db_active_count, "index_count": 0}
    index_count = index["pages_indexed"]
    status = "clean" if db_active_count == index_count else "drifted"
    return {
        "status":      status,
        "db_count":    db_active_count,
        "index_count": index_count,
        "generated_at": index["generated_at"],
    }


def _extract_title(page: dict) -> str:
    """Return H1 title from content, falling back to the page title field."""
    m = re.match(r"^#\s+(.+)", page["content"].lstrip())
    return m.group(1).strip() if m else page.get("title", page["path"])


def _snippet(content: str, q_lower: str) -> str:
    """Return ~300-char excerpt centred on first match."""
    idx = content.lower().find(q_lower)
    if idx < 0:
        return ""
    start = max(0, idx - _SNIPPET_RADIUS)
    end   = min(len(content), idx + len(q_lower) + _SNIPPET_RADIUS)
    snippet = content[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(content):
        snippet = snippet + "…"
    return snippet
