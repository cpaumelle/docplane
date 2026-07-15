import hashlib
import hmac
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import psycopg2.errors
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import Gauge, generate_latest, CONTENT_TYPE_LATEST

from app.db import get_conn
from app.models import (
    AddRedirectRequest,
    AddRedirectResult,
    DeployResult,
    NavMovePage,
    NavMoveRequest,
    NavMoveResult,
    NavView,
    PageIn,
    PageMoveRequest,
    PageMoveResult,
    PageRow,
    PageSummary,
    RedirectOrphan,
    RedirectReconcileResult,
    SectionInfo,
    SectionOrderIn,
    SectionOrderView,
    VersionDetail,
    VersionSummary,
)
from app import generator, lifecycle, lint_rules, search

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Public-facing URLs are env-overridable so this service is deployable outside
# the CharlieHub fabric (self-hosted installs). Defaults preserve hub2 behavior.
_API_PUBLIC_URL = os.environ.get("DOCS_API_PUBLIC_URL", "https://docs-api.charliehub.net").rstrip("/")
_DOCS_URL = os.environ.get("DOCS_GUIDE_URL", "https://docs.charliehub.net/agent-guides/docs-api/")

# Opt-in lockdown for /api/agent-config. Default OFF: on the CharlieHub fabric the
# endpoint is anonymous BY DESIGN (network-position auth via the internal-only
# middleware — see the endpoint docstring). Self-hosted installs that don't have a
# network boundary in front of the API should set DOCS_AGENT_CONFIG_REQUIRE_KEY=true,
# which makes the endpoint require X-API-Key like every other /api/docs/* endpoint
# (at that point it is a config echo, not a bootstrap).
_AGENT_CONFIG_REQUIRE_KEY = os.environ.get(
    "DOCS_AGENT_CONFIG_REQUIRE_KEY", "false"
).strip().lower() in ("1", "true", "yes")


def _ensure_sections_schema_and_seed() -> None:
    """Idempotent: create docs.sections if missing, seed bootstrap defaults if empty.
    Safe to run on every startup — CREATE TABLE IF NOT EXISTS and conditional seed."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS docs.sections (
              name        TEXT      PRIMARY KEY,
              sort_order  INTEGER   NOT NULL,
              updated_at  TIMESTAMP NOT NULL DEFAULT now(),
              updated_by  TEXT      NOT NULL DEFAULT 'unknown',
              CONSTRAINT chk_section_name CHECK (name = btrim(name) AND name !~ '/' AND name <> 'Home')
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS docs_sections_order_idx ON docs.sections(sort_order)")
        cur.execute("SELECT COUNT(*) FROM docs.sections")
        if cur.fetchone()[0] == 0:
            for name, order in generator.DEFAULT_SECTION_ORDER.items():
                cur.execute(
                    "INSERT INTO docs.sections (name, sort_order, updated_by) "
                    "VALUES (%s, %s, 'bootstrap') ON CONFLICT (name) DO NOTHING",
                    (name, order),
                )
            log.info("docs.sections seeded from DEFAULT_SECTION_ORDER (%d rows)",
                     len(generator.DEFAULT_SECTION_ORDER))
        conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        _ensure_sections_schema_and_seed()
    except Exception as exc:   # don't block startup on DB hiccups; deploy path falls back to defaults
        log.error("section bootstrap failed: %s", exc)
    _start_deploy_worker()
    yield


app = FastAPI(title="Docs Control Plane", version="1.3.0", lifespan=lifespan)

_API_KEY = os.environ["DOCS_API_KEY"]


def _fetch_section_order() -> dict[str, int]:
    """Read current section ordering from DB. Falls back to defaults if the
    table is empty or missing so deploys never break on a misconfig."""
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name, sort_order FROM docs.sections")
            rows = cur.fetchall()
    except Exception as exc:
        log.warning("section order fetch failed, using defaults: %s", exc)
        return dict(generator.DEFAULT_SECTION_ORDER)
    if not rows:
        return dict(generator.DEFAULT_SECTION_ORDER)
    return {name: order for name, order in rows}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    for e in errors:
        e["docs"] = _DOCS_URL + "#field-reference"
        # Pydantic v2 model_validator puts the raw exception in ctx["error"] — not JSON-serializable
        if "ctx" in e and isinstance(e["ctx"].get("error"), Exception):
            e["ctx"] = {"error": str(e["ctx"]["error"])}
    return JSONResponse(status_code=422, content={"detail": errors})


def _require_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    # Constant-time comparison to avoid leaking the key via timing. hmac.compare_digest
    # requires both operands non-empty (and same type); a missing header must never
    # short-circuit to a match.
    if not x_api_key or not _API_KEY or not hmac.compare_digest(x_api_key, _API_KEY):
        raise HTTPException(
            status_code=403,
            detail=(
                "Invalid or missing API key. "
                "Bootstrap from any fabric node: "
                f"KEY=$(curl -s {_API_PUBLIC_URL}/api/agent-config | jq -r .auth.key). "
                f"If running directly on the host, use the helper script or resolve the container IP first — see {_DOCS_URL}"
            ),
        )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "agent": {
            "bootstrap": "/api/agent-config",
            "base_url": _API_PUBLIC_URL,
        },
    }

_START_TIME = datetime.now(timezone.utc).isoformat()
_GIT_SHA = os.getenv("GIT_SHA", "unknown")
_BUILD_TIMESTAMP = os.getenv("BUILD_TIMESTAMP", "unknown")
_DEPLOY_ID = os.getenv("DEPLOY_ID", "unknown")


@app.get("/api/docs/version", include_in_schema=False)
def docs_version():
    """Code provenance — used by deploy-control-plane.sh alignment poll. No auth required."""
    started = datetime.fromisoformat(_START_TIME)
    uptime = int((datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds())
    return {
        "status": "ok",
        "git_sha": _GIT_SHA,
        "build_timestamp": _BUILD_TIMESTAMP,
        "deploy_id": _DEPLOY_ID,
        "started_at": _START_TIME,
        "uptime_seconds": uptime,
    }



@app.get("/api/agent-config")
def agent_config(x_api_key: Optional[str] = Header(default=None)):
    """No app-layer auth BY DESIGN (on the fabric): docs-api.charliehub.net is reachable
    only from fabric IPs via the internal-only Traefik middleware, so network position IS
    the authentication layer (IMDS-style bootstrap — see agent-guides/docs-api 'Bootstrap').
    Returns everything an agent on any fabric node needs to start using this API.

    Deployments WITHOUT such a network boundary (e.g. self-hosted installs) must set
    DOCS_AGENT_CONFIG_REQUIRE_KEY=true, which makes this endpoint require X-API-Key.

    NOTE (2026-07-04): the Track C audit closed this endpoint with _require_key(), but that
    broke the documented fabric bootstrap AND scripts/docs-update.sh (both bootstrap the key
    from here), and contradicted the guide's stated IMDS design. Reverted. Closing it is a
    DESIGN change that must land the guide rewrite + every consumer (docs-update.sh,
    phase0-preflight.sh, env provisioning for non-hub2 fabric nodes) in the same window — not
    a lone in-app lockdown. The other /api/docs/* endpoints stay key-authed as before.
    The env gate above exists for non-fabric deployments and stays OFF here."""
    if _AGENT_CONFIG_REQUIRE_KEY:
        _require_key(x_api_key)
    return {
        "service": "docs-api",
        "base_url": _API_PUBLIC_URL,
        "auth": {
            "type": "api_key",
            "header": "X-API-Key",
            "key": _API_KEY,
        },
        "quickstart": [
            f"KEY=$(curl -s {_API_PUBLIC_URL}/api/agent-config | jq -r .auth.key)",
            f"curl -H \"X-API-Key: $KEY\" {_API_PUBLIC_URL}/api/docs/pages",
            f"curl -H \"X-API-Key: $KEY\" '{_API_PUBLIC_URL}/api/docs/search?q=your+search+term'",
            f"curl -s {_API_PUBLIC_URL}/openapi.json | jq '.paths | keys'",
        ],
        "invariants": [
            "NEVER edit /docs-content/*.md directly — changes are silently overwritten by the next deploy",
            "ALL page writes go through PUT /api/docs/pages/{path}",
            "ALL reads go through GET /api/docs/pages/{path}",
            "archived pages are excluded from deploy and search — use POST /api/docs/pages/{path}/restore first",
        ],
        "docs": _DOCS_URL,
    }


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/api/docs/pages", response_model=list[PageSummary])
def list_pages(
    status: str = "active",
    x_api_key: Optional[str] = Header(default=None),
):
    _require_key(x_api_key)
    if status not in ("active", "inactive", "archived", "all"):
        raise HTTPException(status_code=400, detail="status must be 'active', 'inactive', 'archived', or 'all'")
    with get_conn() as conn:
        cur = conn.cursor()
        if status == "all":
            cur.execute(
                "SELECT path, title, nav_path, version, status, updated_at, updated_by "
                "FROM docs.pages ORDER BY path"
            )
        else:
            cur.execute(
                "SELECT path, title, nav_path, version, status, updated_at, updated_by "
                "FROM docs.pages WHERE status = %s ORDER BY path",
                (status,),
            )
        rows = cur.fetchall()
    return [
        PageSummary(path=r[0], title=r[1], nav_path=r[2], version=r[3],
                    status=r[4], updated_at=r[5], updated_by=r[6])
        for r in rows
    ]


@app.get("/api/docs/nav", response_model=NavView)
def get_nav(x_api_key: Optional[str] = Header(default=None)):
    """Rendered nav tree for active pages — the same structure written into
    mkdocs.yml. Use this before writing a page to discover existing section
    names (case/whitespace matter) rather than inventing new ones.

    Returns:
      - tree: mkdocs-shaped list, e.g. [{"Home": "index.md"}, {"Services": [...]}]
      - section_paths: flat list like ["Services", "Services/Spacen", ...]
      - top_level_sections: first-level section names in render order
      - conflicts: best-effort list of pages currently breaking the tree
                   (empty in a healthy deployment)
    """
    _require_key(x_api_key)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT path, nav_path FROM docs.pages WHERE status = 'active' ORDER BY path"
        )
        pages = [{"path": r[0], "nav_path": r[1]} for r in cur.fetchall()]
    tree, conflicts = generator.build_nav(
        pages, strict=False, section_order=_fetch_section_order()
    )
    top_level = [key for entry in tree for key in entry]
    return NavView(
        tree=tree,
        section_paths=generator.distinct_section_paths(pages),
        top_level_sections=top_level,
        conflicts=conflicts,
    )


def _prefix_matches(nav_path: str, prefix: str) -> bool:
    return nav_path == prefix or nav_path.startswith(prefix + "/")


def _rewrite_prefix(nav_path: str, from_prefix: str, to_prefix: str) -> str:
    if nav_path == from_prefix:
        return to_prefix
    if nav_path.startswith(from_prefix + "/"):
        return to_prefix + nav_path[len(from_prefix):]
    return nav_path


@app.post("/api/docs/nav/move", response_model=NavMoveResult)
def nav_move(req: NavMoveRequest, x_api_key: Optional[str] = Header(default=None)):
    """Atomically rename a nav_path prefix across every matching active page.

    DEPRECATED — prefer POST /api/docs/nav/reparent which has identical semantics
    and a name that accurately reflects the operation (only nav_path/sidebar
    presentation changes; resource identity/URLs are unchanged).

    Example: move everything under 'Services/CharlieHub Infrastructure' to
    'Services/Infrastructure' in one transaction. Each moved page gets a new
    revision (version+1) and a snapshot in page_versions. Triggers a single
    deploy at the end. No If-Match required (bulk op); concurrency is handled
    by SELECT FOR UPDATE on matching rows.

    Returns 422 if the result would conflict with existing sections.
    Returns moved=0 if nothing matched (no-op is not an error).
    """
    _require_key(x_api_key)

    with get_conn() as conn:
        cur = conn.cursor()

        # Load every active page's nav-relevant fields. Lock only rows we will
        # modify; non-matching rows are read-only for the simulation.
        cur.execute(
            "SELECT path, nav_path FROM docs.pages WHERE status = 'active' ORDER BY path"
        )
        all_pages = [{"path": r[0], "nav_path": r[1]} for r in cur.fetchall()]

        matching_paths = [
            p["path"] for p in all_pages if _prefix_matches(p["nav_path"], req.from_prefix)
        ]

        if not matching_paths:
            return NavMoveResult(
                moved=0, from_prefix=req.from_prefix, to_prefix=req.to_prefix, pages=[]
            )

        # Lock the rows we're about to change
        cur.execute(
            "SELECT path, nav_path, content, title, revision, version "
            "FROM docs.pages WHERE path = ANY(%s) AND status = 'active' FOR UPDATE",
            (matching_paths,),
        )
        locked = cur.fetchall()
        # Re-read nav_path from the lock — another transaction might have raced us
        locked_map = {r[0]: r for r in locked}
        if set(locked_map) != set(matching_paths):
            raise HTTPException(
                status_code=409,
                detail="Concurrent modification — some matching pages were changed or archived during the move. Retry.",
            )

        # Simulate the resulting tree with every match rewritten
        simulated = [
            {
                "path": p["path"],
                "nav_path": _rewrite_prefix(p["nav_path"], req.from_prefix, req.to_prefix)
                if _prefix_matches(p["nav_path"], req.from_prefix)
                else p["nav_path"],
            }
            for p in all_pages
        ]
        try:
            generator.build_nav(simulated)
        except generator.NavConflict as e:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "nav_path conflict — move would break the deploy",
                    "kind": e.kind,
                    "segment": e.segment,
                    "message": e.message,
                    "hint": "GET /api/docs/nav to inspect the current tree",
                },
            )

        # Apply moves, snapshotting each prior state
        moved: list[NavMovePage] = []
        for path in matching_paths:
            _, old_nav, content, title, revision, version = locked_map[path]
            new_nav = _rewrite_prefix(old_nav, req.from_prefix, req.to_prefix)
            cur.execute(
                "INSERT INTO docs.page_versions "
                "(path, content, revision, updated_by, title, nav_path, version) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (path, content, revision, req.updated_by, title, old_nav, version),
            )
            cur.execute(
                "UPDATE docs.pages SET nav_path=%s, revision=gen_random_uuid()::text, "
                "version=version+1, updated_at=now(), updated_by=%s "
                "WHERE path=%s RETURNING revision, version",
                (new_nav, req.updated_by, path),
            )
            new_rev, new_ver = cur.fetchone()
            moved.append(NavMovePage(
                path=path, old_nav_path=old_nav, new_nav_path=new_nav,
                revision=new_rev, version=new_ver,
            ))

        conn.commit()

    _schedule_deploy()
    return NavMoveResult(
        moved=len(moved), from_prefix=req.from_prefix, to_prefix=req.to_prefix, pages=moved,
    )


@app.post("/api/docs/nav/reparent", response_model=NavMoveResult)
def nav_reparent(req: NavMoveRequest, x_api_key: Optional[str] = Header(default=None)):
    """Preferred alias for POST /api/docs/nav/move.

    Reparents pages under a new nav_path prefix — sidebar presentation only.
    Resource identity (URL/path) is unchanged. Use POST /api/docs/pages/move
    to relocate a page's canonical URL.
    """
    return nav_move(req, x_api_key=x_api_key)


@app.get("/api/docs/nav/sections", response_model=SectionOrderView)
def get_section_order(x_api_key: Optional[str] = Header(default=None)):
    """Current top-level section ordering used by the nav renderer.

    Returns known sections in configured order plus any sections that are in
    use by active pages but not in the docs.sections table (those sort
    alphabetically after all configured ones)."""
    _require_key(x_api_key)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT name, sort_order FROM docs.sections ORDER BY sort_order, name")
        configured = cur.fetchall()
        cur.execute(
            "SELECT split_part(nav_path, '/', 1) AS top, COUNT(*) "
            "FROM docs.pages WHERE status = 'active' AND nav_path <> 'Home' "
            "GROUP BY top"
        )
        usage = {row[0]: row[1] for row in cur.fetchall()}

    configured_names = {name for name, _ in configured}
    sections = [
        SectionInfo(name=name, sort_order=order, page_count=usage.get(name, 0))
        for name, order in configured
    ]
    unordered = sorted(name for name in usage if name not in configured_names)
    return SectionOrderView(sections=sections, unordered_sections=unordered)


@app.put("/api/docs/nav/sections", response_model=SectionOrderView)
def put_section_order(
    req: SectionOrderIn,
    x_api_key: Optional[str] = Header(default=None),
):
    """Replace the top-level section ordering in a single transaction.

    sort_order is assigned from the array index (0-based). Names not in the
    new list are deleted from the table and will sort alphabetically after
    configured ones. Sections in use by pages but absent here are reported
    under `unordered_sections` in the response so the caller can see the
    consequences of their list.

    Triggers a background deploy so the rendered nav reflects the change.
    """
    _require_key(x_api_key)

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM docs.sections")
        for idx, name in enumerate(req.order):
            cur.execute(
                "INSERT INTO docs.sections (name, sort_order, updated_by) "
                "VALUES (%s, %s, %s)",
                (name, idx, req.updated_by),
            )
        conn.commit()

    _schedule_deploy()
    return get_section_order(x_api_key=x_api_key)


@app.get("/api/docs/pages/{path:path}/history", response_model=list[VersionSummary])
def page_history(path: str, x_api_key: Optional[str] = Header(default=None)):
    _require_key(x_api_key)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT revision, version, updated_by, archived_at "
            "FROM docs.page_versions WHERE path = %s ORDER BY archived_at DESC",
            (path,),
        )
        rows = cur.fetchall()
    if not rows:
        # Check the page exists at all
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM docs.pages WHERE path = %s", (path,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Page not found")
    return [VersionSummary(revision=r[0], version=r[1], updated_by=r[2], archived_at=r[3])
            for r in rows]


@app.get("/api/docs/pages/{path:path}/versions/{revision}", response_model=VersionDetail)
def get_version(path: str, revision: str, x_api_key: Optional[str] = Header(default=None)):
    _require_key(x_api_key)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT path, title, nav_path, content, revision, version, updated_by, archived_at "
            "FROM docs.page_versions WHERE path = %s AND revision = %s",
            (path, revision),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Version not found")
    return VersionDetail(path=row[0], title=row[1], nav_path=row[2], content=row[3],
                         revision=row[4], version=row[5], updated_by=row[6], archived_at=row[7])


@app.get("/api/docs/pages/{path:path}", response_model=PageRow)
def get_page(
    path: str,
    response: Response,
    include_archived: bool = False,
    x_api_key: Optional[str] = Header(default=None),
):
    _require_key(x_api_key)
    with get_conn() as conn:
        cur = conn.cursor()
        if include_archived:
            cur.execute(
                "SELECT path, title, nav_path, content, revision, version, status, updated_at, updated_by "
                "FROM docs.pages WHERE path = %s",
                (path,),
            )
        else:
            cur.execute(
                "SELECT path, title, nav_path, content, revision, version, status, updated_at, updated_by "
                "FROM docs.pages WHERE path = %s AND status = 'active'",
                (path,),
            )
        row = cur.fetchone()
        if not row:
            suggestions = []
            if not path.endswith(".md"):
                candidates = [f"{path}/index.md", f"{path}.md"]
                placeholders = ",".join(["%s"] * len(candidates))
                cur.execute(
                    f"SELECT path FROM docs.pages WHERE path IN ({placeholders}) AND status = 'active'",
                    candidates,
                )
                suggestions = [r[0] for r in cur.fetchall()]
            detail: dict = {"error": "Page not found", "path": path}
            if suggestions:
                detail["did_you_mean"] = suggestions
            raise HTTPException(status_code=404, detail=detail)
    response.headers["ETag"] = f'"{row[4]}"'
    return PageRow(path=row[0], title=row[1], nav_path=row[2], content=row[3],
                   revision=row[4], version=row[5], status=row[6],
                   updated_at=row[7], updated_by=row[8])


@app.post("/api/docs/pages", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
def create_page_stub():
    raise HTTPException(
        status_code=405,
        detail="Use PUT /api/docs/pages/{path} to create or update a page.",
    )


def _validate_nav_change(incoming_path: str, incoming_nav_path: str) -> None:
    """Simulate the nav tree with this page's nav_path applied — raise 422
    if the write would break the next deploy. Catches leaf-vs-section conflicts
    and case/whitespace collisions against existing sections."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT path, nav_path FROM docs.pages "
            "WHERE status = 'active' AND path != %s",
            (incoming_path,),
        )
        simulated = [{"path": r[0], "nav_path": r[1]} for r in cur.fetchall()]
    simulated.append({"path": incoming_path, "nav_path": incoming_nav_path})
    try:
        generator.build_nav(simulated)
    except generator.NavConflict as e:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "nav_path conflict — would break next deploy",
                "kind": e.kind,
                "segment": e.segment,
                "message": e.message,
                "hint": "GET /api/docs/nav to see the current tree and existing section names",
            },
        )


def _nav_top_section(nav_path: str) -> str:
    """Top-level nav section (first '/'-segment) of a nav_path."""
    return nav_path.split("/", 1)[0]


def _nav_majority_section(sibling_nav_paths: list[str]) -> Optional[tuple[str, int, int]]:
    """If a strict majority of siblings share one top-level nav section, return
    (section, dominant_count, total); else None.

    Strict majority (``dominant*2 > total``) — not unanimity — so a single already
    mis-filed sibling cannot poison the consensus and blind the guard for the whole
    namespace, while a namespace that *legitimately* spans sections with no >50%
    section (e.g. the ``services/`` root: Infrastructure / Network / Products & Clients
    / Services) yields no majority and is left alone."""
    tops = [_nav_top_section(n) for n in sibling_nav_paths if n]
    if not tops:
        return None
    counts: dict[str, int] = {}
    for t in tops:
        counts[t] = counts.get(t, 0) + 1
    dominant = max(counts, key=counts.get)
    dom_count = counts[dominant]
    if dom_count * 2 <= len(tops):
        return None
    return dominant, dom_count, len(tops)


def _nav_sibling_decision(
    parent: str, sibling_nav_paths: list[str], incoming_path: str, incoming_nav_path: str
) -> Optional[tuple[str, str]]:
    """Pure decision (no I/O). Returns ``(level, message)`` or None, where ``level`` is:

    - ``"block"`` — the incoming top-level nav section is the *title-cased first
      URL-path segment* (e.g. path ``services/...`` filed under ``Services/...``) AND
      diverges from the strict-majority sibling section. This is the precise fingerprint
      of "the author inferred the nav section from the URL path", which is almost never
      a real intent — high enough confidence to reject (overridable).
    - ``"warn"`` — a strict-majority divergence that is NOT the path-derived fingerprint.
      Could be a deliberate cross-section move, so advise but never block.

    ``path`` (URL identity) and ``nav_path`` (curated sidebar section) are intentionally
    decoupled, which is why only the path-derived case is hard enough to block."""
    majority = _nav_majority_section(sibling_nav_paths)
    if majority is None:
        return None
    dominant, dom_count, total = majority
    incoming_top = _nav_top_section(incoming_nav_path)
    if incoming_top == dominant:
        return None
    base = (
        f"nav_path section {incoming_top!r} diverges from {dominant!r}, the section used "
        f"by {dom_count} of {total} existing page(s) under {parent + '/'!r}."
    )
    first_seg = incoming_path.split("/", 1)[0]
    if incoming_top.casefold() == first_seg.casefold():
        # path-derived fingerprint — block (overridable)
        return (
            "block",
            base + f" This looks like the section was inferred from the URL path segment "
            f"{first_seg!r}, but path and nav_path are decoupled — the page belongs under "
            f"{dominant!r}. If this divergence is genuinely intended, resend with header "
            f"'X-Nav-Override: true'.",
        )
    return (
        "warn",
        base + f" path and nav_path are intentionally decoupled, so this is a warning, not "
        f"an error — but if the section is wrong, re-file under {dominant!r}. "
        f"GET /api/docs/nav to see the tree.",
    )


def _load_immediate_siblings(incoming_path: str) -> Optional[tuple[str, list[str]]]:
    """Return (parent_dir, [nav_path,...]) for active pages in the same path directory
    as ``incoming_path`` (excluding it), or None if the page is top-level."""
    parent = os.path.dirname(incoming_path)
    if not parent:
        return None  # a top-level page has no namespace to compare against
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT path, nav_path FROM docs.pages "
            "WHERE status = 'active' AND path LIKE %s AND path != %s",
            (parent + "/%", incoming_path),
        )
        rows = cur.fetchall()
    # LIKE 'parent/%' also matches deeper descendants — keep only immediate siblings
    # (same directory), so 'services/zoho-books-api' is not compared against
    # 'services/foo/bar.md'.
    return parent, [nav for (p, nav) in rows if os.path.dirname(p) == parent]


def _nav_section_decision(incoming_path: str, incoming_nav_path: str) -> Optional[tuple[str, str]]:
    """I/O wrapper around _nav_sibling_decision over the immediate path-siblings."""
    loaded = _load_immediate_siblings(incoming_path)
    if loaded is None:
        return None
    parent, siblings = loaded
    return _nav_sibling_decision(parent, siblings, incoming_path, incoming_nav_path)


def _count_nav_divergences(pages: list[tuple[str, str]]) -> int:
    """Pure: count active pages that are *path-derived* mis-files — i.e. the page's nav
    top-level section equals the title-cased first URL-path segment AND diverges from
    its namespace's strict-majority section (the same condition the write path BLOCKS).

    Deliberately NOT the broad "any divergence" count: ``path`` and ``nav_path`` are
    decoupled by design, so legitimate cross-section filing is common and correct (e.g.
    ``operations/*`` evidence pages filed under ``Archive/...`` per the lifecycle model).
    Counting those would make the gauge perpetually non-zero and the alert noise. This
    counts only the actionable mis-file class — standing URL-derived mis-files the block
    didn't prevent (pre-guard pages, or override-bypassed writes) — so ``> 0`` is real."""
    by_parent: dict[str, list[tuple[str, str]]] = {}
    for path, nav in pages:
        by_parent.setdefault(os.path.dirname(path), []).append((path, nav))
    total = 0
    for parent, items in by_parent.items():
        if not parent:
            continue
        majority = _nav_majority_section([nav for _p, nav in items])
        if majority is None:
            continue
        dominant = majority[0]
        for path, nav in items:
            if not nav:
                continue
            top = _nav_top_section(nav)
            first_seg = path.split("/", 1)[0]
            if top != dominant and top.casefold() == first_seg.casefold():
                total += 1
    return total


@app.put("/api/docs/pages/{path:path}")
def upsert_page(
    path: str,
    page: PageIn,
    response: Response,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    x_api_key: Optional[str] = Header(default=None),
    x_nav_override: Optional[str] = Header(default=None, alias="X-Nav-Override"),
):
    """Create or update a docs page. Runnable as one call from any fabric node.

    - New page: no If-Match needed → created.
    - Update with `If-Match: <revision>`: strict optimistic lock (412 on mismatch).
    - Update with `If-Match: *`: upsert — overwrite the latest in one call (no prior
      GET), read-modify-write under the row lock. Opt-in last-writer-wins.
    - Update with no If-Match: 428 (must choose a concrete revision or `*`).
    """
    _require_key(x_api_key)

    # Reject nav_path conflicts before taking any row locks
    _validate_nav_change(path, page.nav_path)

    # Nav-section sibling guard. If this page's nav section diverges from the
    # strict-majority section of its immediate path-siblings, decide block vs warn:
    #   block — the section is the title-cased URL-path segment (the "inferred the
    #           section from the URL path" mis-file); reject 422 unless overridden.
    #   warn  — a non-path-derived divergence (possibly a deliberate move); advise only.
    # This enforces correctness at the API for ALL writers — the helper, the MCP tool,
    # or a raw curl — because the API, not the wrapper, is the control boundary.
    nav_warning = None
    nav_decision = _nav_section_decision(path, page.nav_path)
    if nav_decision is not None:
        level, msg = nav_decision
        overridden = (x_nav_override or "").strip().lower() in ("1", "true", "yes")
        if level == "block" and not overridden:
            log.warning("nav section BLOCK on write to %s: %s", path, msg)
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "nav_path section mismatch — likely inferred from the URL path",
                    "message": msg,
                    "hint": "path (URL) and nav_path (sidebar section) are decoupled; file "
                            "under the section your path-siblings use, or override if deliberate",
                    "override_header": "X-Nav-Override: true",
                },
            )
        nav_warning = msg + (" [X-Nav-Override accepted]" if level == "block" else "")
        log.warning("nav section %s on write to %s%s: %s",
                    level, path, " (overridden)" if level == "block" else "", msg)

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT revision, content, title, nav_path, version, status "
            "FROM docs.pages WHERE path = %s FOR UPDATE",
            (path,),
        )
        existing = cur.fetchone()

        if existing is not None:
            ex_revision, ex_content, ex_title, ex_nav_path, ex_version, ex_status = existing
            if ex_status == 'archived':
                raise HTTPException(status_code=409, detail=f"Page is archived — restore it first with POST /api/docs/pages/{path}/restore (requires If-Match from GET /api/docs/pages/{path}?include_archived=true)")
            if if_match is None:
                raise HTTPException(
                    status_code=428,
                    detail=f"If-Match header required for updates — either GET /api/docs/pages/{path} for the current revision UUID and send If-Match: <revision> (optimistic lock), or send If-Match: * to overwrite the latest in one call (upsert)",
                )
            provided_revision = if_match.strip('"')
            # If-Match: *  → upsert / overwrite-latest in a single call. The read-modify-
            # write happens here under the row's FOR UPDATE lock, so it is race-free (no
            # client GET→PUT window). Any other value is a strict optimistic-lock check.
            # This is what lets *any* fabric node update a page with one curl — no prior
            # revision fetch, no helper script, no SSH. Concurrency protection is preserved
            # for callers who pass a concrete revision; '*' is an explicit opt-in to
            # last-writer-wins.
            if provided_revision != "*" and provided_revision != ex_revision:
                raise HTTPException(
                    status_code=412,
                    detail=f"Revision conflict — your revision is stale. Re-fetch with GET /api/docs/pages/{path} to get the current revision and retry (or send If-Match: * to overwrite the latest)",
                )

            # Snapshot current state before overwriting
            cur.execute(
                "INSERT INTO docs.page_versions "
                "(path, content, revision, updated_by, title, nav_path, version) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (path, ex_content, ex_revision, page.updated_by,
                 ex_title, ex_nav_path, ex_version),
            )
            cur.execute(
                "UPDATE docs.pages SET title=%s, nav_path=%s, content=%s, updated_by=%s, "
                "updated_at=now(), revision=gen_random_uuid()::text, version=version+1 "
                "WHERE path=%s RETURNING revision, version",
                (page.title, page.nav_path, page.content, page.updated_by, path),
            )
            new_revision, new_version = cur.fetchone()
            conn.commit()
            response.headers["ETag"] = f'"{new_revision}"'
            _schedule_deploy()
            result = {"path": path, "revision": new_revision, "version": new_version, "action": "updated"}
            if nav_warning:
                result["warnings"] = [nav_warning]
            return result

        else:
            try:
                cur.execute(
                    "INSERT INTO docs.pages (path, title, nav_path, content, updated_by) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING revision, version",
                    (path, page.title, page.nav_path, page.content, page.updated_by),
                )
            except psycopg2.errors.CheckViolation:
                conn.rollback()
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid path '{path}': must match ^[a-z0-9/_-]+\\.md$ (include the .md extension)",
                )
            new_revision, new_version = cur.fetchone()
            conn.commit()
            response.status_code = status.HTTP_201_CREATED
            response.headers["ETag"] = f'"{new_revision}"'
            _schedule_deploy()
            result = {"path": path, "revision": new_revision, "version": new_version, "action": "created"}
            if nav_warning:
                result["warnings"] = [nav_warning]
            return result


@app.delete("/api/docs/pages/{path:path}", status_code=status.HTTP_204_NO_CONTENT)
def delete_page(
    path: str,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    x_api_key: Optional[str] = Header(default=None),
):
    _require_key(x_api_key)

    if if_match is None:
        raise HTTPException(
            status_code=428,
            detail=f"If-Match header required — GET /api/docs/pages/{path} to read the current revision UUID, then include If-Match: <revision>. Consider POST /api/docs/pages/{path}/archive instead (soft delete, recoverable)",
        )

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT revision, content, title, nav_path, version, status "
            "FROM docs.pages WHERE path = %s FOR UPDATE",
            (path,),
        )
        existing = cur.fetchone()

        if not existing:
            raise HTTPException(status_code=404, detail=f"Page not found — use GET /api/docs/pages to list all active pages")

        ex_revision, ex_content, ex_title, ex_nav_path, ex_version, ex_status = existing
        provided_revision = if_match.strip('"')
        if provided_revision != ex_revision:
            raise HTTPException(
                status_code=412,
                detail=f"Revision conflict — your revision is stale. Re-fetch with GET /api/docs/pages/{path} to get the current revision and retry",
            )

        cur.execute(
            "INSERT INTO docs.page_versions "
            "(path, content, revision, updated_by, title, nav_path, version) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (path, ex_content, ex_revision, "deleted",
             ex_title, ex_nav_path, ex_version),
        )
        cur.execute("DELETE FROM docs.pages WHERE path = %s", (path,))
        conn.commit()

    _schedule_deploy()


@app.post("/api/docs/pages/move", response_model=PageMoveResult, status_code=201)
def move_page(
    req: PageMoveRequest,
    x_api_key: Optional[str] = Header(default=None),
):
    """Atomically relocate a page to a new canonical path (URL-identity operation).

    Archives from_path and creates to_path in a single transaction. Optionally
    adds a redirect_maps entry in mkdocs.yml so old URLs continue to resolve.

    Unlike nav/reparent, this changes the resource identity (URL/path), not just
    sidebar presentation. The new page's nav_path is taken from new_nav_path if
    provided, or inherited from the source page.

    Returns 404 if from_path not found or not active.
    Returns 409 if to_path already exists (active or archived).
    """
    _require_key(x_api_key)

    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            "SELECT title, nav_path, content, revision, version, status "
            "FROM docs.pages WHERE path = %s FOR UPDATE",
            (req.from_path,),
        )
        source = cur.fetchone()
        if not source:
            raise HTTPException(status_code=404, detail=f"Source page not found: {req.from_path}")
        src_title, src_nav_path, src_content, src_revision, src_version, src_status = source
        if src_status != "active":
            raise HTTPException(
                status_code=409,
                detail=f"Source page {req.from_path!r} is {src_status!r} — only active pages can be moved",
            )

        cur.execute("SELECT status FROM docs.pages WHERE path = %s", (req.to_path,))
        dest = cur.fetchone()
        if dest:
            raise HTTPException(
                status_code=409,
                detail=f"Destination {req.to_path!r} already exists (status={dest[0]!r}) — choose a different path or delete the existing record first",
            )

        new_nav_path = req.new_nav_path if req.new_nav_path is not None else src_nav_path

        cur.execute(
            "INSERT INTO docs.page_versions "
            "(path, content, revision, updated_by, title, nav_path, version) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (req.from_path, src_content, src_revision, req.updated_by,
             src_title, src_nav_path, src_version),
        )

        cur.execute(
            "INSERT INTO docs.pages (path, title, nav_path, content, updated_by) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING revision, version",
            (req.to_path, src_title, new_nav_path, src_content, req.updated_by),
        )
        new_revision, new_version = cur.fetchone()

        cur.execute(
            "UPDATE docs.pages SET status='archived', updated_at=now(), updated_by=%s "
            "WHERE path = %s",
            (req.updated_by, req.from_path),
        )

        conn.commit()

    redirect_added = False
    repair_required = False
    recommended_action = None
    if req.create_redirect:
        try:
            generator.add_redirect(req.from_path, req.to_path)
            redirect_added = True
        except Exception as e:
            log.error("redirect add failed (non-fatal — page move committed): %s", e)
            repair_required = True
            recommended_action = f"POST /api/docs/pages/{req.to_path}/add-redirect with body {{\"from_path\": \"{req.from_path}\"}}"

    _schedule_deploy()
    return PageMoveResult(
        from_path=req.from_path,
        to_path=req.to_path,
        nav_path=new_nav_path,
        revision=new_revision,
        version=new_version,
        redirect_added=redirect_added,
        repair_required=repair_required,
        recommended_action=recommended_action,
    )


@app.post("/api/docs/pages/{path:path}/rollback/{revision}")
def rollback(
    path: str,
    revision: str,
    x_api_key: Optional[str] = Header(default=None),
):
    _require_key(x_api_key)

    with get_conn() as conn:
        cur = conn.cursor()

        # Fetch the historical version to restore
        cur.execute(
            "SELECT title, nav_path, content FROM docs.page_versions "
            "WHERE path = %s AND revision = %s",
            (path, revision),
        )
        historical = cur.fetchone()
        if not historical:
            raise HTTPException(status_code=404, detail="Version not found")
        old_title, old_nav_path, old_content = historical

        # Get current page (must exist)
        cur.execute(
            "SELECT revision, content, title, nav_path, version "
            "FROM docs.pages WHERE path = %s FOR UPDATE",
            (path,),
        )
        current = cur.fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Page not found")

        # Snapshot current state before rollback
        cur.execute(
            "INSERT INTO docs.page_versions "
            "(path, content, revision, updated_by, title, nav_path, version) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (path, current[1], current[0], f"pre-rollback:{revision}",
             current[2], current[3], current[4]),
        )

        # Restore historical content as new current version
        cur.execute(
            "UPDATE docs.pages SET title=%s, nav_path=%s, content=%s, "
            "revision=gen_random_uuid()::text, version=version+1, "
            "updated_at=now(), updated_by=%s WHERE path=%s RETURNING revision, version",
            (old_title, old_nav_path, old_content, f"rollback:{revision}", path),
        )
        new_revision, new_version = cur.fetchone()
        conn.commit()

    return {"path": path, "new_revision": new_revision, "version": new_version,
            "rolled_back_from": revision}


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------


@app.get("/api/docs/lint")
def lint(x_api_key: Optional[str] = Header(default=None)):
    _require_key(x_api_key)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT path, content FROM docs.pages WHERE status = 'active' ORDER BY path")
        pages = cur.fetchall()
    errors, warnings = lint_rules.lint_findings(pages)
    return {
        "pages_scanned": len(pages),
        "errors": errors,
        "warnings": warnings,
        "passed": len(errors) == 0,
    }


@app.get("/api/docs/lifecycle/check")
def lifecycle_check(x_api_key: Optional[str] = Header(default=None)):
    """Validate the lifecycle declaration on every active page (F7.2).

    The agent guide referenced this endpoint as an active validator, but it previously
    404'd (documented-but-not-implemented). This IS that endpoint. Rules in
    `app.lifecycle.lifecycle_findings`. Same response shape as /api/docs/lint."""
    _require_key(x_api_key)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT path, content FROM docs.pages WHERE status = 'active' ORDER BY path")
        pages = cur.fetchall()
    errors, warnings = lifecycle.lifecycle_findings(pages)
    return {
        "pages_scanned": len(pages),
        "valid_states": sorted(lifecycle.VALID_LIFECYCLE_STATES),
        "errors": errors,
        "warnings": warnings,
        "passed": len(errors) == 0,
    }


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@app.get("/api/docs/search")
def search_pages(
    q: str,
    limit: int = 20,
    x_api_key: Optional[str] = Header(default=None),
):
    _require_key(x_api_key)
    if not q.strip():
        raise HTTPException(status_code=400, detail="q must be non-empty")
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be 1–100")
    return search.search(q.strip(), limit=limit)


@app.get("/api/docs/search/status")
def search_status(x_api_key: Optional[str] = Header(default=None)):
    _require_key(x_api_key)
    return search.index_status()


@app.get("/api/docs/search/drift")
def search_drift(x_api_key: Optional[str] = Header(default=None)):
    _require_key(x_api_key)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM docs.pages WHERE status = 'active'")
        db_count = cur.fetchone()[0]
    return search.index_drift(db_count)


# ---------------------------------------------------------------------------
# Archive / Restore
# ---------------------------------------------------------------------------

@app.post("/api/docs/pages/{path:path}/archive")
def archive_page(
    path: str,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    x_api_key: Optional[str] = Header(default=None),
):
    _require_key(x_api_key)
    if if_match is None:
        raise HTTPException(
            status_code=428,
            detail=f"If-Match header required — GET /api/docs/pages/{path} to read the current revision UUID, then include If-Match: <revision>",
        )

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT revision, content, title, nav_path, version, status "
            "FROM docs.pages WHERE path = %s FOR UPDATE",
            (path,),
        )
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail=f"Page not found — use GET /api/docs/pages to list all active pages")
        ex_revision, ex_content, ex_title, ex_nav_path, ex_version, ex_status = existing
        provided_revision = if_match.strip('"')
        if provided_revision != ex_revision:
            raise HTTPException(
                status_code=412,
                detail=f"Revision conflict — your revision is stale. Re-fetch with GET /api/docs/pages/{path} to get the current revision and retry",
            )
        if ex_status == 'archived':
            raise HTTPException(status_code=409, detail="Page is already archived — use POST /api/docs/pages/{path}/restore to make it active again")

        cur.execute(
            "INSERT INTO docs.page_versions "
            "(path, content, revision, updated_by, title, nav_path, version) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (path, ex_content, ex_revision, "archive", ex_title, ex_nav_path, ex_version),
        )
        cur.execute(
            "UPDATE docs.pages SET status='archived', updated_at=now(), updated_by='archive', "
            "revision=gen_random_uuid()::text, version=version+1 "
            "WHERE path=%s RETURNING revision, version",
            (path,),
        )
        new_revision, new_version = cur.fetchone()
        conn.commit()

    _schedule_deploy()
    return {"path": path, "status": "archived", "revision": new_revision, "version": new_version}


@app.post("/api/docs/pages/{path:path}/restore")
def restore_page(
    path: str,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    x_api_key: Optional[str] = Header(default=None),
):
    _require_key(x_api_key)
    if if_match is None:
        raise HTTPException(
            status_code=428,
            detail=f"If-Match header required — GET /api/docs/pages/{path}?include_archived=true to read the current revision UUID, then include If-Match: <revision>",
        )

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT revision, content, title, nav_path, version, status "
            "FROM docs.pages WHERE path = %s FOR UPDATE",
            (path,),
        )
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail=f"Page not found — archived pages are not returned by default; try GET /api/docs/pages/{path}?include_archived=true")
        ex_revision, ex_content, ex_title, ex_nav_path, ex_version, ex_status = existing
        provided_revision = if_match.strip('"')
        if provided_revision != ex_revision:
            raise HTTPException(
                status_code=412,
                detail=f"Revision conflict — your revision is stale. Re-fetch with GET /api/docs/pages/{path}?include_archived=true to get the current revision and retry",
            )
        if ex_status != 'archived':
            raise HTTPException(status_code=409, detail="Page is not archived — use POST /api/docs/pages/{path}/archive to archive it first")

        cur.execute(
            "INSERT INTO docs.page_versions "
            "(path, content, revision, updated_by, title, nav_path, version) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (path, ex_content, ex_revision, "restore", ex_title, ex_nav_path, ex_version),
        )
        cur.execute(
            "UPDATE docs.pages SET status='active', updated_at=now(), updated_by='restore', "
            "revision=gen_random_uuid()::text, version=version+1 "
            "WHERE path=%s RETURNING revision, version",
            (path,),
        )
        new_revision, new_version = cur.fetchone()
        conn.commit()

    _schedule_deploy()
    return {"path": path, "status": "active", "revision": new_revision, "version": new_version}


@app.post("/api/docs/pages/{path:path}/add-redirect", response_model=AddRedirectResult)
def add_page_redirect(
    path: str,
    req: AddRedirectRequest,
    x_api_key: Optional[str] = Header(default=None),
):
    """Idempotent redirect repair: ensure a redirect_maps entry exists from req.from_path to path.

    Use this to repair a pages/move that committed in DB but returned redirect_added=false.
    Safe to retry: returns success=true whether the redirect was newly written or already present.

    Validates:
    - path (destination) is an active page
    - from_path is archived or absent (prevents redirecting away from a live page)
    Does NOT modify page DB records — only appends to mkdocs.yml redirect_maps.
    """
    _require_key(x_api_key)

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM docs.pages WHERE path = %s", (path,))
        dest = cur.fetchone()
        if not dest or dest[0] != "active":
            raise HTTPException(
                status_code=404,
                detail=f"Destination page {path!r} not found or not active",
            )
        cur.execute("SELECT status FROM docs.pages WHERE path = %s", (req.from_path,))
        src = cur.fetchone()
        if src and src[0] == "active":
            raise HTTPException(
                status_code=409,
                detail=f"Source {req.from_path!r} is still active — cannot redirect away from a live page",
            )

    try:
        newly_added = generator.add_redirect(req.from_path, path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write redirect: {e}")

    if newly_added:
        _schedule_deploy()

    return AddRedirectResult(
        success=True,
        redirect_added=newly_added,
        from_path=req.from_path,
        to_path=path,
    )


# ---------------------------------------------------------------------------
# Deploy worker — I-DOCS-DEPLOY-1
#
# Event-based coalescing: every write calls _schedule_deploy() which sets an
# event flag.  A single persistent daemon thread waits on the flag, clears it
# BEFORE acquiring the lock (so writes during deploy re-signal it), then
# deploys from the current DB state.  No trigger is ever silently dropped;
# rapid write bursts are coalesced into a single deploy from the latest state.
# ---------------------------------------------------------------------------

_DEPLOY_LOCK = threading.Lock()
_deploy_event = threading.Event()


def _schedule_deploy() -> None:
    """Signal that a deploy is needed. Returns immediately; worker coalesces."""
    _deploy_event.set()


def _deploy_worker() -> None:
    """Persistent daemon — wakes on _deploy_event, deploys from latest DB state."""
    while True:
        _deploy_event.wait()
        _deploy_event.clear()  # clear BEFORE lock so writes during deploy re-signal
        with _DEPLOY_LOCK:
            try:
                with get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT path, nav_path, content, updated_at, version FROM docs.pages "
                        "WHERE status = 'active' ORDER BY path"
                    )
                    rows = cur.fetchall()
                pages = [{"path": r[0], "nav_path": r[1], "content": r[2], "updated_at": r[3], "version": r[4]}
                         for r in rows]
                generator.run(pages, section_order=_fetch_section_order())
                search.build_index(pages)
                _fire_mcp_webhook()
            except Exception as e:
                log.error("background deploy failed: %s", e)


def _start_deploy_worker() -> None:
    t = threading.Thread(target=_deploy_worker, daemon=True, name="deploy-worker")
    t.start()
    log.info("deploy worker started")


# ---------------------------------------------------------------------------
# MCP reindex webhooks — fired after each deploy completes
# ---------------------------------------------------------------------------
# Each MCP target gets a POST /reindex with a Bearer token. The MCP polls
# INDEX_PATH on the mkdocs site volume and rebuilds its Lunr index on
# webhook receipt — much faster than the legacy periodic refresh.
#
# Two configuration shapes are supported:
#   DOCS_MCP_WEBHOOK_URL=<url>          (single, legacy) — uses DOCS_MCP_API_KEY
#   DOCS_MCP_WEBHOOKS=<url>=<key>[,...] (multi, current) — per-URL bearer key
# Multi-target lets a single docs-api fan out to OLD and NEW MCPs during
# the migration; once OLD is retired the env var collapses to one entry.
# Failures are logged but never block the deploy path.

_DOCS_MCP_WEBHOOK_URL = os.environ.get("DOCS_MCP_WEBHOOK_URL", "")
_DOCS_MCP_API_KEY     = os.environ.get("DOCS_MCP_API_KEY", "")
_DOCS_MCP_WEBHOOKS    = os.environ.get("DOCS_MCP_WEBHOOKS", "")


def _parse_webhooks() -> list[tuple[str, str]]:
    """Return list of (url, bearer_key) tuples from env vars."""
    out: list[tuple[str, str]] = []
    if _DOCS_MCP_WEBHOOKS:
        for spec in _DOCS_MCP_WEBHOOKS.split(","):
            spec = spec.strip()
            if not spec or "=" not in spec:
                continue
            url, key = spec.split("=", 1)
            url, key = url.strip(), key.strip()
            if url and key:
                out.append((url, key))
    if _DOCS_MCP_WEBHOOK_URL and _DOCS_MCP_API_KEY:
        out.append((_DOCS_MCP_WEBHOOK_URL, _DOCS_MCP_API_KEY))
    return out


def _fire_mcp_webhook():
    targets = _parse_webhooks()
    if not targets:
        return
    try:
        import requests as _requests
    except Exception as e:
        log.warning("docs-mcp webhook skipped (requests missing): %s", e)
        return
    for url, key in targets:
        try:
            _requests.post(
                url,
                headers={"Authorization": f"Bearer {key}"},
                timeout=2,
            )
            log.info("docs-mcp webhook fired: %s", url)
        except Exception as e:
            log.warning("docs-mcp webhook failed (non-fatal) %s: %s", url, e)


# ---------------------------------------------------------------------------
# Deploy
# ---------------------------------------------------------------------------

@app.post("/api/docs/deploy", response_model=DeployResult)
def deploy(dry_run: bool = False, x_api_key: Optional[str] = Header(default=None)):
    _require_key(x_api_key)

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT path, nav_path, content, updated_at, version FROM docs.pages "
            "WHERE status = 'active' ORDER BY path"
        )
        rows = cur.fetchall()

    pages = [{"path": r[0], "nav_path": r[1], "content": r[2], "updated_at": r[3], "version": r[4]} for r in rows]

    with _DEPLOY_LOCK:
        try:
            result = generator.run(pages, dry_run=dry_run, section_order=_fetch_section_order())
        except generator.NavConflict as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "nav_path conflict in active pages",
                    "kind": exc.kind,
                    "segment": exc.segment,
                    "message": exc.message,
                    "hint": "GET /api/docs/nav to inspect conflicts before retrying",
                },
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    if not dry_run:
        search.build_index(pages)

    return DeployResult(**result)


# ---------------------------------------------------------------------------
# Render (dry-run with per-file hashes — closes CCM Gap 2)
# ---------------------------------------------------------------------------

def _content_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode()).hexdigest()


@app.post("/api/docs/render")
def render(x_api_key: Optional[str] = Header(default=None)):
    """Compute what deploy would write without writing anything.
    Returns nav tree, per-file hash comparison, and canonical checksum."""
    _require_key(x_api_key)

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT path, nav_path, content, updated_at, version FROM docs.pages "
            "WHERE status = 'active' ORDER BY path"
        )
        rows = cur.fetchall()

    pages = [{"path": r[0], "nav_path": r[1], "content": r[2], "updated_at": r[3], "version": r[4]} for r in rows]
    section_order = _fetch_section_order()

    nav_block, _ = generator.build_nav(pages, strict=False, section_order=section_order)

    # Per-file rendered hashes
    rendered_hashes: dict[str, str] = {}
    for page in pages:
        rendered = generator._augment_content(page)
        rendered_hashes[page["path"]] = _content_hash(rendered)

    # Current on-disk hashes
    disk_hashes: dict[str, str] = {}
    if os.path.isdir(generator.DOCS_CONTENT_DIR):
        for dirpath, _, filenames in os.walk(generator.DOCS_CONTENT_DIR):
            for fname in filenames:
                if not fname.endswith(".md"):
                    continue
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, generator.DOCS_CONTENT_DIR)
                with open(full) as f:
                    disk_hashes[rel] = _content_hash(f.read())

    all_paths = sorted(set(rendered_hashes) | set(disk_hashes))
    files = []
    summary = {"unchanged": 0, "changed": 0, "new": 0, "orphaned": 0}
    for path in all_paths:
        rh = rendered_hashes.get(path)
        dh = disk_hashes.get(path)
        if rh and dh:
            file_status = "unchanged" if rh == dh else "changed"
        elif rh and not dh:
            file_status = "new"
        else:
            file_status = "orphaned"
        summary[file_status] += 1
        if file_status != "unchanged":
            files.append({
                "path": path,
                "rendered_hash": rh,
                "current_hash": dh,
                "status": file_status,
            })

    # Canonical checksum — order-stable, locked formula
    checksum_input = "\n".join(
        sorted(f"{p}:{h}" for p, h in rendered_hashes.items())
    )
    checksum = "sha256:" + hashlib.sha256(checksum_input.encode()).hexdigest()

    return {
        "nav": nav_block,
        "pages_total": len(pages),
        "checksum": checksum,
        "files": files,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Drift (content-based DB vs disk comparison — closes CCM Gap 4)
# ---------------------------------------------------------------------------

@app.get("/api/docs/drift")
def drift(x_api_key: Optional[str] = Header(default=None)):
    """Compare DB active pages against rendered files on disk.
    Returns per-file status with generated/manual classification."""
    _require_key(x_api_key)

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT path, nav_path, content, updated_at, version FROM docs.pages "
            "WHERE status = 'active' ORDER BY path"
        )
        rows = cur.fetchall()

    pages = [{"path": r[0], "nav_path": r[1], "content": r[2], "updated_at": r[3], "version": r[4]} for r in rows]

    # Expected rendered hashes from DB
    rendered_hashes: dict[str, str] = {}
    for page in pages:
        rendered = generator._augment_content(page)
        rendered_hashes[page["path"]] = _content_hash(rendered)

    # Actual on-disk state
    disk_files: dict[str, tuple[str, str]] = {}  # path → (hash, origin)
    if os.path.isdir(generator.DOCS_CONTENT_DIR):
        for dirpath, _, filenames in os.walk(generator.DOCS_CONTENT_DIR):
            for fname in filenames:
                if not fname.endswith(".md"):
                    continue
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, generator.DOCS_CONTENT_DIR)
                with open(full) as f:
                    content = f.read()
                origin = "generated" if content.startswith(generator.GENERATOR_STAMP) else "manual"
                disk_files[rel] = (_content_hash(content), origin)

    all_paths = sorted(set(rendered_hashes) | set(disk_files))
    files = []
    clean_count = 0
    has_generated_drift = False
    has_manual_files = False
    has_orphans = False

    for path in all_paths:
        rh = rendered_hashes.get(path)
        disk_info = disk_files.get(path)

        if rh and disk_info:
            dh, origin = disk_info
            if rh == dh:
                clean_count += 1
                continue
            file_status = "drifted"
            if origin == "generated":
                has_generated_drift = True
            files.append({
                "path": path, "status": file_status, "origin": origin,
                "db_hash": rh, "disk_hash": dh,
            })
        elif rh and not disk_info:
            files.append({
                "path": path, "status": "missing_on_disk", "origin": "unknown",
            })
            has_generated_drift = True
        else:
            _, origin = disk_info
            files.append({
                "path": path, "status": "orphaned_on_disk", "origin": origin,
            })
            has_orphans = True
            if origin == "manual":
                has_manual_files = True

    invariants = {
        "no_generated_drift": not has_generated_drift,
        "no_manual_files": not has_manual_files,
        "no_orphans": not has_orphans,
    }

    if has_generated_drift:
        top_status = "drifted"
    elif has_manual_files or has_orphans:
        top_status = "inconsistent"
    else:
        top_status = "clean"

    result = {
        "status": top_status,
        "invariants": invariants,
        "db_active_count": len(pages),
        "clean": clean_count,
        "files": files,
    }

    # Update Prometheus gauges
    _update_drift_metrics(result)

    return result


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

_DRIFT_TOTAL = Gauge(
    "docs_drift_total", "Count of docs files by drift status",
    ["status"],
)
_DRIFT_INVARIANT = Gauge(
    "docs_drift_invariant", "Docs drift invariant status (1=ok, 0=violated)",
    ["name"],
)
_NAV_DIVERGENCE = Gauge(
    "docs_nav_section_divergence",
    "Count of active pages whose nav top-level section diverges from their "
    "path-namespace's strict-majority section (0 = consistent)",
)


def _update_nav_divergence_metric() -> None:
    """Recompute the standing nav-section divergence count and set the gauge.
    Recomputed on each /metrics scrape; failures must not break the endpoint."""
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT path, nav_path FROM docs.pages WHERE status = 'active'")
            pages = cur.fetchall()
        _NAV_DIVERGENCE.set(_count_nav_divergences(pages))
    except Exception as exc:  # pragma: no cover - metric must never 500 /metrics
        log.warning("nav divergence metric update failed: %s", exc)


def _update_drift_metrics(drift_result: dict) -> None:
    """Push drift results into Prometheus gauges."""
    # Count by status
    counts = {"clean": drift_result["clean"], "drifted": 0, "missing": 0, "orphaned": 0}
    for f in drift_result["files"]:
        if f["status"] == "drifted":
            counts["drifted"] += 1
        elif f["status"] == "missing_on_disk":
            counts["missing"] += 1
        elif f["status"] == "orphaned_on_disk":
            counts["orphaned"] += 1
    for s, v in counts.items():
        _DRIFT_TOTAL.labels(status=s).set(v)

    for name, ok in drift_result["invariants"].items():
        _DRIFT_INVARIANT.labels(name=name).set(1 if ok else 0)


@app.get("/api/docs/redirects/reconcile", response_model=RedirectReconcileResult)
def reconcile_redirects(x_api_key: Optional[str] = Header(default=None)):
    """Read-only redirect health check.

    Parses the redirect_maps block from mkdocs.yml and classifies each entry as
    healthy or orphaned based on current DB page statuses. Does NOT modify any state.

    Healthy:  from_path is archived or absent AND to_path is active.
    Orphaned: to_path is archived/missing, OR from_path is still active.

    Orphan reasons:
      destination_archived  — to_path exists but is archived
      destination_missing   — to_path not in DB (hard-deleted)
      source_still_active   — from_path is still an active page
    """
    _require_key(x_api_key)

    redirects = generator.parse_redirect_maps()

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT path, status FROM docs.pages")
        db_pages: dict[str, str] = {row[0]: row[1] for row in cur.fetchall()}

    orphans: list[RedirectOrphan] = []
    healthy_count = 0

    for from_path, to_path in redirects.items():
        from_status = db_pages.get(from_path)  # None = hard-deleted or never in DB
        to_status = db_pages.get(to_path)

        from_ok = from_status in (None, "archived")
        to_ok = to_status == "active"

        if from_ok and to_ok:
            healthy_count += 1
        else:
            if to_status == "archived":
                reason = "destination_archived"
            elif to_status is None:
                reason = "destination_missing"
            elif from_status == "active":
                reason = "source_still_active"
            else:
                reason = f"destination_status_{to_status}"
            orphans.append(RedirectOrphan(from_path=from_path, to_path=to_path, reason=reason))

    return RedirectReconcileResult(
        healthy=len(orphans) == 0,
        orphans=orphans,
        summary={
            "total_redirects": len(redirects),
            "healthy": healthy_count,
            "orphans": len(orphans),
        },
    )


@app.get("/metrics")
def metrics():
    """Prometheus metrics endpoint."""
    _update_nav_divergence_metric()
    return PlainTextResponse(
        content=generate_latest().decode(),
        media_type=CONTENT_TYPE_LATEST,
    )
