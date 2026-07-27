"""Runtime publication and certification services."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import requests

from app import generator, search
from app.db import get_conn

log = logging.getLogger(__name__)
_DEPLOY_LOCK = threading.Lock()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def load_release_state(conn) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, str]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT resource_id::text, path, title, nav_path, content, revision,
               version, status, updated_at
          FROM docs.pages
         ORDER BY path
        """
    )
    rows = cur.fetchall()
    pages = [
        {
            "resource_id": row[0],
            "path": row[1],
            "title": row[2],
            "nav_path": row[3],
            "content": row[4],
            "revision": row[5],
            "version": row[6],
            "status": row[7],
            "updated_at": row[8],
        }
        for row in rows
    ]
    cur.execute("SELECT name, sort_order FROM docs.sections ORDER BY sort_order, name")
    sections = {row[0]: int(row[1]) for row in cur.fetchall()}
    cur.execute("SELECT from_path, to_path FROM docs.redirects ORDER BY from_path")
    redirects = {row[0]: row[1] for row in cur.fetchall()}
    return pages, sections, redirects


def state_identity(pages: list[dict[str, Any]], sections: dict[str, int], redirects: dict[str, str]) -> str:
    return _canonical_hash(
        {
            "pages": [
                {
                    "resource_id": page["resource_id"],
                    "path": page["path"],
                    "nav_path": page["nav_path"],
                    "revision": page["revision"],
                    "status": page["status"],
                }
                for page in sorted(pages, key=lambda item: item["path"])
            ],
            "sections": sections,
            "redirects": redirects,
        }
    )


def _fire_webhooks() -> None:
    raw = os.environ.get("DOCPLANE_PUBLICATION_WEBHOOKS", "")
    for specification in filter(None, (part.strip() for part in raw.split(","))):
        url, separator, token = specification.partition("=")
        if not separator or not url.strip() or not token.strip():
            log.warning("ignoring invalid publication webhook specification")
            continue
        try:
            requests.post(
                url.strip(),
                headers={"Authorization": f"Bearer {token.strip()}"},
                timeout=3,
            ).raise_for_status()
        except Exception as exc:  # publication remains successful; integration catches up later
            log.warning("publication webhook failed for %s: %s", url.strip(), exc)


def deploy_current_state(*, requested_by: str, change_id: str | None = None) -> dict[str, Any]:
    """Build and promote the current database state, recording a durable attempt."""
    cycle_id = str(uuid4())
    with _DEPLOY_LOCK:
        with get_conn() as conn:
            pages, sections, redirects = load_release_state(conn)
            active = [page for page in pages if page["status"] == "active"]
            target_identity = state_identity(pages, sections, redirects)
            page_identity = _canonical_hash(
                [(page["path"], page["revision"]) for page in sorted(active, key=lambda item: item["path"])]
            )
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO docs.deployment_attempts
                    (deployment_cycle_id, change_id, requested_by, status,
                     target_page_identity, target_state_identity, started_at)
                VALUES (%s, %s, %s, 'BUILDING', %s, %s, now())
                """,
                (cycle_id, change_id, requested_by, page_identity, target_identity),
            )
            conn.commit()

        try:
            result = generator.run(active, section_order=sections)
            search.build_index(active)
            release_id = result["release_id"]
            seal = _canonical_hash(
                {
                    "cycle_id": cycle_id,
                    "release_id": release_id,
                    "target_state_identity": target_identity,
                    "page_identity": page_identity,
                }
            )
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    UPDATE docs.deployment_attempts
                       SET status = 'COMPLETED', release_id = %s, sealed_digest = %s,
                           completed_at = now(), receipt = %s::jsonb
                     WHERE deployment_cycle_id = %s
                    """,
                    (release_id, seal, json.dumps(result, sort_keys=True, default=str), cycle_id),
                )
                cur.execute(
                    """
                    UPDATE docs.corpus_certification
                       SET state = 'CURRENT', working_state_identity = %s,
                           deployed_state_identity = %s, deployment_cycle_id = %s,
                           release_id = %s, last_failure = NULL,
                           state_version = state_version + 1,
                           last_certified_at = now(), updated_at = now()
                     WHERE singleton = true
                    """,
                    (target_identity, target_identity, cycle_id, release_id),
                )
                conn.commit()
            _fire_webhooks()
            return {
                "status": "COMPLETED",
                "deployment_cycle_id": cycle_id,
                "release_id": release_id,
                "target_state_identity": target_identity,
                "sealed_digest": seal,
                **result,
            }
        except Exception as exc:
            failure = {"type": type(exc).__name__, "message": str(exc)[:2000]}
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    UPDATE docs.deployment_attempts
                       SET status = 'FAILED', completed_at = now(), failure = %s::jsonb
                     WHERE deployment_cycle_id = %s
                    """,
                    (json.dumps(failure, sort_keys=True), cycle_id),
                )
                cur.execute(
                    """
                    UPDATE docs.corpus_certification
                       SET state = 'DEPLOYMENT_FAILED', working_state_identity = %s,
                           deployment_cycle_id = %s, last_failure = %s::jsonb,
                           state_version = state_version + 1, updated_at = now()
                     WHERE singleton = true
                    """,
                    (target_identity, cycle_id, json.dumps(failure, sort_keys=True),),
                )
                conn.commit()
            return {
                "status": "FAILED",
                "deployment_cycle_id": cycle_id,
                "target_state_identity": target_identity,
                "failure": failure,
            }


def certification_status() -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT state, working_state_identity, deployed_state_identity,
                   deployment_cycle_id, release_id, last_failure,
                   state_version, last_certified_at, updated_at
              FROM docs.corpus_certification
             WHERE singleton = true
            """
        )
        row = cur.fetchone()
    keys = (
        "state", "working_state_identity", "deployed_state_identity",
        "deployment_cycle_id", "release_id", "last_failure",
        "state_version", "last_certified_at", "updated_at",
    )
    return dict(zip(keys, row)) if row else {"state": "UNAVAILABLE"}
