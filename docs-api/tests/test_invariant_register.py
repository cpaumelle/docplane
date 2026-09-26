"""Invariants register: git YAML -> one generated know-domain register page.

Source contract, deterministic rendering, id anchors, demotion flagging,
decision links, the deployed path/nav contracts, and the publication flow
against an in-memory API (no invariant is ever a model entity).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import invariant_register as ir  # noqa: E402

REGISTER = "control-plane/invariants/register.md"
PRESENCE = "control-plane/invariants/index.md"

SOURCE = """
schema_version: 1
domain: example-observability
owner: example-observability
title: Example Observability invariants
invariants:
  - id: I-COVERAGE-DERIVED-1
    title: Operational coverage derives from authoritative inventory
    statement: |
      Any control whose correctness depends on covering a set of objects MUST derive
      that set from authoritative inventory.
    must_be_true:
      - No population list is hardcoded at a call site.
    ratification: RATIFIED
    enforcement: PARTIAL
    criticality: IMPORTANT
    verification_state: UNVERIFIED
    established_by: operations/decisions/coverage-derivation.md
    enforcement_refs:
      - "CI: coverage registry validator"
  - id: I-OBS-LIVENESS-1
    title: Transport telemetry is not a liveness signal
    statement: Transport telemetry MUST NOT be used as a liveness signal {e.g. a handshake age}.
    ratification: RATIFIED
    enforcement: ENFORCED
    criticality: OPERATIONAL_CRITICAL
    verification_state: VERIFIED
    verified_at: "2026-09-25"
    verified_against: rule source at a pinned commit
    specializes: I-COVERAGE-DERIVED-1
    enforced_by:
      - Vps3NodeDown
  - id: I-PROBE-TRUST-1
    title: A doctrine nothing enforces yet
    statement: Probes MUST share the trust assumptions of the plane they validate.
    ratification: RATIFIED
    enforcement: DOCTRINE_ONLY
    criticality: NORMAL
    verification_state: UNVERIFIED
"""


def _source(tmp_path: Path, text: str = SOURCE, name: str = "example-observability.yaml") -> Path:
    (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def _load(tmp_path: Path, text: str = SOURCE) -> dict:
    return ir.load_sources(_source(tmp_path, text))


def _render(tmp_path: Path, text: str = SOURCE) -> dict:
    domains = _load(tmp_path, text)
    return ir.render_register(domains, ir.fingerprint(domains), REGISTER)


# ── Source contract ─────────────────────────────────────────────────────────

def test_valid_source_loads_deterministically(tmp_path):
    domains = _load(tmp_path)
    assert [r["id"] for r in domains["example-observability"]["invariants"]] == [
        "I-COVERAGE-DERIVED-1", "I-OBS-LIVENESS-1", "I-PROBE-TRUST-1"]
    assert ir.fingerprint(domains) == ir.fingerprint(ir.load_sources(tmp_path))


def test_fingerprint_moves_when_a_record_moves(tmp_path):
    base = ir.fingerprint(_load(tmp_path))
    assert ir.fingerprint(ir.load_sources(_source(tmp_path, SOURCE.replace("criticality: IMPORTANT", "criticality: NORMAL")))) != base


@pytest.mark.parametrize(
    "mutation, finding",
    [
        (lambda s: s.replace("    ratification: RATIFIED\n    enforcement: PARTIAL", "    enforcement: PARTIAL"), "ratification is required"),
        (lambda s: s.replace("enforcement: PARTIAL", "enforcement: MOSTLY"), "enforcement must be one of"),
        (lambda s: s.replace("id: I-OBS-LIVENESS-1", "id: OBS-LIVENESS"), "id must match"),
        (lambda s: s.replace('    verified_at: "2026-09-25"\n', ""), "verified_at is required when verification_state is VERIFIED"),
        (lambda s: s.replace("    enforced_by:\n      - Vps3NodeDown\n", ""), "enforcement ENFORCED requires enforced_by or enforcement_refs"),
        (lambda s: s.replace("established_by: operations/decisions/coverage-derivation.md", "established_by: https://example/adr"), "established_by must be a DocPlane page path"),
        (lambda s: s.replace("    must_be_true:", "    detector: x\n    must_be_true:"), "unknown field 'detector'"),
        (lambda s: s.replace("title: Example Observability invariants\n", ""), "title is required"),
        (lambda s: s.replace("id: I-PROBE-TRUST-1", "id: I-OBS-LIVENESS-1"), "duplicate id in file"),
    ],
)
def test_contract_violations_fail_closed_with_every_finding(tmp_path, mutation, finding):
    with pytest.raises(ir.SourceError) as caught:
        _load(tmp_path, mutation(SOURCE))
    assert any(finding in item for item in caught.value.findings), caught.value.findings


def test_an_id_belongs_to_exactly_one_domain(tmp_path):
    _source(tmp_path)
    _source(tmp_path, SOURCE.replace("domain: example-observability", "domain: example-edge"), name="example-edge.yaml")
    with pytest.raises(ir.SourceError) as caught:
        ir.load_sources(tmp_path)
    assert any("already declared in" in item for item in caught.value.findings)


# ── Rendering ───────────────────────────────────────────────────────────────

def test_one_register_page_with_bare_id_headings_as_anchors(tmp_path):
    page = _render(tmp_path)
    assert page == _render(tmp_path)
    assert page["path"] == REGISTER
    body = page["content"]
    assert "**Lifecycle:** REFERENCE" in body and "Edit them there, never here." in body
    assert "\n## example-observability\n" in body
    assert "\n### I-COVERAGE-DERIVED-1\n" in body and "](#i-coverage-derived-1)" in body
    assert "{#" not in body
    assert "`Vps3NodeDown`" in body
    assert "[I-COVERAGE-DERIVED-1](#i-coverage-derived-1) |" in body  # specializes links in-page
    assert "&#123;e.g. a handshake age&#125;" in body


def test_unenforced_records_are_flagged_for_demotion(tmp_path):
    domains = _load(tmp_path)
    flagged = [record["id"] for _, record in ir.iter_records(domains) if ir.demotion_candidate(record)]
    assert flagged == ["I-PROBE-TRUST-1"]
    body = ir.render_register(domains, ir.fingerprint(domains), REGISTER)["content"]
    assert "1 flagged for demotion" in body
    section = body.split("### I-PROBE-TRUST-1", 1)[1].split("### ", 1)[0]
    assert "**Demotion candidate**" in section
    assert "DOCTRINE_ONLY · demotion candidate" in body
    superseded = SOURCE.replace("title: A doctrine nothing enforces yet\n    statement: Probes MUST share the trust assumptions of the plane they validate.\n    ratification: RATIFIED",
                                "title: A doctrine nothing enforces yet\n    statement: Probes MUST share the trust assumptions of the plane they validate.\n    ratification: SUPERSEDED")
    domains = ir.load_sources(_source(tmp_path, superseded))
    assert not any(ir.demotion_candidate(record) for _, record in ir.iter_records(domains))


def test_establishing_decision_is_a_relative_page_link(tmp_path):
    body = _render(tmp_path)["content"]
    assert "| Established by | [operations/decisions/coverage-derivation.md](../../operations/decisions/coverage-derivation.md) |" in body


def test_heading_anchor_matches_the_site_slugger(tmp_path):
    markdown = pytest.importorskip("markdown")
    html = markdown.markdown(_render(tmp_path)["content"], extensions=["tables", "toc"])
    for anchor in ("i-coverage-derived-1", "i-obs-liveness-1", "example-observability"):
        assert f'id="{anchor}"' in html


def test_paths_and_nav_satisfy_the_deployed_contracts(tmp_path):
    from app.generator import _insert
    from app.publication import _PATH_RE

    page = _render(tmp_path)
    presence = ir.presence_page(PRESENCE, REGISTER)
    tree: dict = {}
    for item in (page, presence):
        assert _PATH_RE.fullmatch(item["path"]), item["path"]
        _insert(tree, item["nav_path"].split(" / "), item["path"])
    assert tree["Control Plane"]["Invariants"] == {"Register": REGISTER, "Overview": PRESENCE}
    assert "](register.md)" in presence["content"]


# ── Publication flow against a fake API ─────────────────────────────────────

class FakeApi:
    def __init__(self, pages: dict[str, dict] | None = None, artifact: dict | None = None, previous: str | None = None):
        self.pages = pages or {}
        self.artifact = artifact
        self.previous = previous
        self.calls: list[tuple[str, str, dict | None]] = []

    def call(self, method: str, path: str, body: dict | None = None, key: str | None = None) -> dict:
        self.calls.append((method, path, body))
        if method == "GET" and path.startswith("/api/v1/pages?"):
            wanted = path.split("path=", 1)[1].split("&", 1)[0]
            return {"pages": [self.pages[wanted]] if wanted in self.pages else []}
        if method == "GET" and path == "/api/v1/model/artifacts":
            return {"artifacts": [self.artifact] if self.artifact else []}
        if method == "GET" and path.endswith("/status"):
            return {"current_status": [{"observation_kind": "GENERATION", "source_fingerprint": self.previous}] if self.previous else []}
        if method == "GET" and path.startswith("/api/v1/model/entities"):
            return {"entities": []}
        if method == "POST" and path == "/api/v1/model/entities":
            return {"entity_id": "system-1", **body}
        if method == "POST" and path == "/api/v1/model/artifacts":
            self.artifact = {"artifact_id": "artifact-1", "version": 1, "status": "DECLARED", "target_page_paths": [], **body}
            return self.artifact
        if method == "POST" and path == "/api/v1/changes":
            return {"change_id": "change-1"}
        if path.endswith("/publish"):
            self.artifact = {**self.artifact, "target_page_paths": body and [] or [REGISTER], "generator_version": ir.GENERATOR_VERSION}
            return {"publication_receipt": {"deployment": {"status": "COMPLETED"}}}
        return {}

    def operations(self) -> list[dict]:
        return [body for method, path, body in self.calls if method == "POST" and path.endswith("/operations")]


def _run(tmp_path, monkeypatch, api: FakeApi, text: str = SOURCE) -> int:
    import schema_catalogue

    monkeypatch.setenv("INVARIANT_SOURCE_DIR", str(_source(tmp_path, text)))
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.example")
    monkeypatch.setattr(ir, "Client", lambda *args, **kwargs: api)
    monkeypatch.setattr(ir, "read_secret", lambda name: "not-a-real-token")
    monkeypatch.setattr(schema_catalogue, "Client", lambda *args, **kwargs: api)
    return ir.main([])


def test_first_publish_creates_register_and_presence_and_never_mints_invariant_entities(tmp_path, monkeypatch):
    api = FakeApi(pages={"operations/decisions/coverage-derivation.md": {"resource_id": "d1", "revision": "r", "status": "active"}})
    assert _run(tmp_path, monkeypatch, api) == 0
    creates = [o for o in api.operations() if o["operation_type"] == "CREATE_PAGE"]
    assert [o["payload"]["path"] for o in creates] == [REGISTER, PRESENCE]
    assert creates[0]["payload"]["knowledge_class"] == "POLICY"
    assert "knowledge_class" not in creates[1]["payload"]  # presence stays hand-curated
    entity_posts = [body for method, path, body in api.calls if method == "POST" and path == "/api/v1/model/entities"]
    assert [body["entity_kind"] for body in entity_posts] == ["SYSTEM"]  # provenance source card only
    change = next(body for method, path, body in api.calls if method == "POST" and path == "/api/v1/changes")
    assert change["generated_ownership_plan"]["target_page_paths"] == [REGISTER]


def test_an_existing_presence_page_is_left_alone(tmp_path, monkeypatch):
    api = FakeApi(pages={
        "operations/decisions/coverage-derivation.md": {"resource_id": "d1", "revision": "r", "status": "active"},
        PRESENCE: {"resource_id": "p1", "revision": "r1", "status": "active"},
    })
    assert _run(tmp_path, monkeypatch, api) == 0
    assert [o["payload"]["path"] for o in api.operations() if o["operation_type"] == "CREATE_PAGE"] == [REGISTER]


def test_an_unresolvable_decision_reference_fails_closed_before_any_change(tmp_path, monkeypatch):
    api = FakeApi()
    with pytest.raises(RuntimeError, match="established_by names pages that do not exist"):
        _run(tmp_path, monkeypatch, api)
    assert not any(path == "/api/v1/changes" for _, path, _ in api.calls)


def test_a_hand_authored_page_at_the_register_path_is_never_adopted(tmp_path, monkeypatch):
    api = FakeApi(pages={REGISTER: {"resource_id": "authored-1", "revision": "r1", "status": "active"}})
    with pytest.raises(RuntimeError, match="refusing to replace it"):
        _run(tmp_path, monkeypatch, api)


def test_unchanged_source_publishes_nothing(tmp_path, monkeypatch):
    domains = _load(tmp_path)
    artifact = {"artifact_id": "artifact-1", "artifact_key": ir.ARTIFACT_KEY, "version": 2, "status": "DECLARED", "target_page_paths": [REGISTER],
                "generator_version": ir.GENERATOR_VERSION, "projection_contract_version": ir.PROJECTION_CONTRACT_VERSION}
    api = FakeApi(artifact=artifact, previous=ir.fingerprint(domains))
    assert _run(tmp_path, monkeypatch, api) == 0
    assert not api.operations()
    assert any(path == "/api/v1/observations" for _, path, _ in api.calls)


# ── CLI ─────────────────────────────────────────────────────────────────────

def test_validate_only_and_dry_run_need_no_api(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INVARIANT_SOURCE_DIR", str(_source(tmp_path)))
    monkeypatch.delenv("DOCPLANE_API", raising=False)
    assert ir.main(["--validate-only"]) == 0
    assert "VALID 3 invariants across 1 domains, 1 flagged for demotion" in capsys.readouterr().out
    assert ir.main(["--dry-run"]) == 0
    assert f"DRY-RUN would publish {REGISTER}" in capsys.readouterr().out


def test_an_unresolved_owner_is_recorded_not_inherited(tmp_path):
    gap = SOURCE.replace(
        "    title: A doctrine nothing enforces yet\n",
        "    title: A doctrine nothing enforces yet\n    owner_unresolved: source names no owner\n",
    )
    domains = _load(tmp_path, gap)
    record = domains["example-observability"]["invariants"][2]
    assert ir.ownership_gap(record)
    body = ir.render_register(domains, ir.fingerprint(domains), REGISTER)["content"]
    section = body.split("### I-PROBE-TRUST-1", 1)[1]
    assert "| Owner | **Unresolved** — source names no owner |" in section
    assert "**Ownership gap**" in section
    assert "| Owner | example-observability |" not in section
    assert "1 with an unresolved owner" in body


def test_owner_and_owner_unresolved_are_mutually_exclusive(tmp_path):
    both = SOURCE.replace(
        "    title: A doctrine nothing enforces yet\n",
        "    title: A doctrine nothing enforces yet\n    owner: someone\n    owner_unresolved: why\n",
    )
    with pytest.raises(ir.SourceError) as caught:
        _load(tmp_path, both)
    assert any("mutually exclusive" in item for item in caught.value.findings)


_UNRATIFIED = SOURCE.replace(
    "    title: A doctrine nothing enforces yet\n    statement: Probes MUST share the trust assumptions of the plane they validate.\n    ratification: RATIFIED\n",
    "    title: A doctrine nothing enforces yet\n    statement: Probes MUST share the trust assumptions of the plane they validate.\n"
    "    ratification: UNRESOLVED\n    ratification_unresolved: the source page never states a ratification\n",
)


def test_an_unresolved_ratification_is_recorded_with_its_reason(tmp_path):
    assert _UNRATIFIED != SOURCE
    domains = _load(tmp_path, _UNRATIFIED)
    record = domains["example-observability"]["invariants"][2]
    assert ir.ratification_gap(record)
    body = ir.render_register(domains, ir.fingerprint(domains), REGISTER)["content"]
    section = body.split("### I-PROBE-TRUST-1", 1)[1]
    assert "| Ratification | **Unresolved** — the source page never states a ratification |" in section
    assert "**Ratification gap**" in section
    assert "1 with unresolved ratification" in body
    assert "| UNRESOLVED |" in body.split("### ", 1)[0]
    # evidence preservation, not an exemption: the enforcement rules still apply
    assert ir.demotion_candidate(record)


def test_an_unresolved_ratification_requires_its_reason(tmp_path):
    bare = _UNRATIFIED.replace("    ratification_unresolved: the source page never states a ratification\n", "")
    with pytest.raises(ir.SourceError) as caught:
        _load(tmp_path, bare)
    assert any("ratification UNRESOLVED requires ratification_unresolved" in item for item in caught.value.findings)


def test_a_ratification_reason_without_the_unresolved_state_is_refused(tmp_path):
    stray = SOURCE.replace(
        "    title: A doctrine nothing enforces yet\n",
        "    title: A doctrine nothing enforces yet\n    ratification_unresolved: why\n",
    )
    with pytest.raises(ir.SourceError) as caught:
        _load(tmp_path, stray)
    assert any("only valid with ratification UNRESOLVED" in item for item in caught.value.findings)


def test_unresolved_ratification_does_not_relax_the_enforcement_rule(tmp_path):
    claimed = _UNRATIFIED.replace(
        "    ratification: UNRESOLVED\n    ratification_unresolved: the source page never states a ratification\n    enforcement: DOCTRINE_ONLY\n",
        "    ratification: UNRESOLVED\n    ratification_unresolved: the source page never states a ratification\n    enforcement: PARTIAL\n",
    )
    assert claimed != _UNRATIFIED
    with pytest.raises(ir.SourceError) as caught:
        _load(tmp_path, claimed)
    assert any("enforcement PARTIAL requires enforced_by or enforcement_refs" in item for item in caught.value.findings)
