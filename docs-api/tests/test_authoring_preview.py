from __future__ import annotations

import os
import uuid

from fastapi.testclient import TestClient

from app.application import app
from app.db import get_conn


client = TestClient(app)

DIALECT_FIXTURE = """---
title: Authoring Fixture
---

# Authoring Fixture {#authoring-fixture}

A paragraph with a [link](https://example.test).

## Attributes {#attributes}

A marked paragraph.
{.important #marked-paragraph}

!!! note "Admonition"
    This must render without changing the source.

=== "First tab"
    Tab content.

## Code {#code}

```python
print("hello")
```

<div data-test="raw-html">HTML stays visible to the renderer.</div>

<!-- source comment retained -->

| Name | Value |
| --- | --- |
| one | two |
"""


def _human_token() -> str:
    os.environ["DOCPLANE_BOOTSTRAP_ADMIN_TOKEN"] = "test-bootstrap-token"
    response = client.post(
        "/api/v1/admin/principals",
        headers={"X-DocPlane-Bootstrap-Token": "test-bootstrap-token"},
        json={
            "principal_kind": "HUMAN",
            "display_name": f"author-{uuid.uuid4().hex[:8]}",
            "scopes": ["docs:read", "docs:propose", "admin:workspaces"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["token"]


def _page(content: str = DIALECT_FIXTURE) -> dict:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT workspace_id FROM docplane.workspaces WHERE workspace_key = 'reference'")
        workspace_id = cur.fetchone()[0]
        suffix = uuid.uuid4().hex[:10]
        cur.execute(
            """
            INSERT INTO docs.pages
                (path, title, nav_path, content, updated_by, workspace_id,
                 publication_state, knowledge_class, verification_state,
                 metadata_review_required)
            VALUES (%s, 'Authoring Fixture', %s, %s, 'authoring-test', %s,
                    'PUBLISHED', 'REFERENCE', 'UNVERIFIED', FALSE)
            RETURNING resource_id::text, revision, content
            """,
            (f"reference/authoring-{suffix}.md", f"Reference/Authoring {suffix}", content, workspace_id),
        )
        resource_id, revision, stored = cur.fetchone()
        conn.commit()
    return {"resource_id": resource_id, "revision": revision, "content": stored}


def test_document_preview_is_lossless_rendered_and_read_only():
    token = _human_token()
    page = _page()
    candidate = page["content"].replace("| one | two |", "| one | updated |")

    response = client.post(
        "/api/v1/authoring/preview",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "page_resource_id": page["resource_id"],
            "expected_revision": page["revision"],
            "scope": "document",
            "content": candidate,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_content"] == candidate
    assert body["candidate_content"] == candidate
    assert body["workspace_key"] == "reference"
    assert body["source_fidelity"]["input_returned_byte_for_byte"] is True
    assert body["source_fidelity"]["database_mutated"] is False
    assert body["operation"]["operation_type"] == "REPLACE_DOCUMENT"
    assert body["operation"]["expected_revision"] == page["revision"]
    assert "-| one | two |" in body["raw_diff"]
    assert "+| one | updated |" in body["raw_diff"]
    assert 'id="authoring-fixture"' in body["rendered_html"]
    assert 'class="admonition note"' in body["rendered_html"]
    assert 'data-test="raw-html"' in body["rendered_html"]
    assert any(item["heading_id"] == "attributes" for item in body["outline"])

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT revision, content FROM docs.pages WHERE resource_id = %s", (page["resource_id"],))
        revision, stored = cur.fetchone()
    assert revision == page["revision"]
    assert stored == page["content"]


def test_section_preview_returns_precise_operation_and_preserves_other_sections():
    token = _human_token()
    page = _page()
    replacement = "## Attributes {#attributes}\n\nReplacement with `{#literal}` inside code.\n"

    response = client.post(
        "/api/v1/authoring/preview",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "page_resource_id": page["resource_id"],
            "expected_revision": page["revision"],
            "scope": "section",
            "heading_id": "attributes",
            "content": replacement,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    operation = body["operation"]
    assert operation["operation_type"] == "REPLACE_SECTION"
    assert operation["expected_section_hash"]
    assert operation["payload"]["heading_id"] == "attributes"
    assert "## Code {#code}" in body["candidate_content"]
    changed = [item for item in body["semantic_diff"] if item["state"] == "changed"]
    assert [item["heading_id"] for item in changed] == ["attributes"]


def test_preview_refuses_stale_revision_and_malformed_source_is_diagnostic():
    token = _human_token()
    page = _page()

    stale = client.post(
        "/api/v1/authoring/preview",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "page_resource_id": page["resource_id"],
            "expected_revision": str(uuid.uuid4()),
            "scope": "document",
            "content": page["content"],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "PAGE_REVISION_STALE"

    malformed = client.post(
        "/api/v1/authoring/preview",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "page_resource_id": page["resource_id"],
            "expected_revision": page["revision"],
            "scope": "document",
            "content": "# Duplicate {#same}\n\n## Again {#same}\n\n```python\nprint('open')\n",
        },
    )
    assert malformed.status_code == 200
    codes = {item["code"] for item in malformed.json()["diagnostics"]}
    assert {"DUPLICATE_EXPLICIT_ID", "UNCLOSED_CODE_FENCE"} <= codes
