"""Invariant catalogue importer: git YAML -> INVARIANT entities -> generated views.

Covers the pure core (source contract, determinism, rendering, anchors, the
deployed path/nav contracts, the INVARIANT card checklist) and lifecycle
reconciliation against an in-memory model API, mirroring the meter-list suite.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import invariant_catalogue as ic  # noqa: E402

SOURCE = """
schema_version: 1
domain: example-observability
owner: example-observability
view:
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
    enforced_by:
      - Vps3NodeDown
"""


def _source(tmp_path: Path, text: str = SOURCE, name: str = "example-observability.yaml") -> Path:
    (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def _load(tmp_path: Path, text: str = SOURCE) -> dict:
    return ic.load_sources(_source(tmp_path, text))


# ── Source contract ─────────────────────────────────────────────────────────

def test_valid_source_loads_deterministically(tmp_path):
    domains = _load(tmp_path)
    assert list(domains) == ["example-observability"]
    assert [r["id"] for r in domains["example-observability"]["invariants"]] == ["I-COVERAGE-DERIVED-1", "I-OBS-LIVENESS-1"]
    assert ic.fingerprint(domains) == ic.fingerprint(ic.load_sources(tmp_path))
    assert len(ic.fingerprint(domains)) == 64


def test_fingerprint_moves_when_a_record_moves(tmp_path):
    base = ic.fingerprint(_load(tmp_path))
    edited = ic.fingerprint(ic.load_sources(_source(tmp_path, SOURCE.replace("enforcement: PARTIAL", "enforcement: ENFORCED"))))
    assert edited != base


@pytest.mark.parametrize(
    "mutation, finding",
    [
        (lambda s: s.replace("    ratification: RATIFIED\n    enforcement: PARTIAL", "    enforcement: PARTIAL"), "ratification is required"),
        (lambda s: s.replace("enforcement: PARTIAL", "enforcement: MOSTLY"), "enforcement must be one of"),
        (lambda s: s.replace("id: I-OBS-LIVENESS-1", "id: OBS-LIVENESS"), "id must match"),
        (lambda s: s.replace('    verified_at: "2026-09-25"\n', ""), "verified_at is required when verification_state is VERIFIED"),
        (lambda s: s.replace("    enforced_by:", "    detector: x\n    enforced_by:"), "unknown field 'detector'"),
        (lambda s: s.replace("schema_version: 1", "schema_version: 2"), "schema_version must be 1"),
        (lambda s: s.replace("id: I-OBS-LIVENESS-1", "id: I-COVERAGE-DERIVED-1"), "duplicate id in file"),
        (lambda s: s.replace('verified_at: "2026-09-25"', "verified_at: last week"), "must be an ISO date"),
    ],
)
def test_contract_violations_fail_closed_with_every_finding(tmp_path, mutation, finding):
    with pytest.raises(ic.SourceError) as caught:
        _load(tmp_path, mutation(SOURCE))
    assert any(finding in item for item in caught.value.findings), caught.value.findings


def test_file_name_must_match_domain(tmp_path):
    with pytest.raises(ic.SourceError) as caught:
        ic.load_sources(_source(tmp_path, name="observability.yaml"))
    assert any("file name must be" in item for item in caught.value.findings)


def test_an_id_belongs_to_exactly_one_domain(tmp_path):
    _source(tmp_path)
    other = SOURCE.replace("domain: example-observability", "domain: example-edge")
    _source(tmp_path, other, name="example-edge.yaml")
    with pytest.raises(ic.SourceError) as caught:
        ic.load_sources(tmp_path)
    assert any("already declared in" in item for item in caught.value.findings)


# ── Rendering ───────────────────────────────────────────────────────────────

def test_one_view_per_domain_with_bare_id_headings_as_stable_anchors(tmp_path):
    domains = _load(tmp_path)
    fp = ic.fingerprint(domains)
    pages = ic.render_pages(domains, fp, "control-plane/invariants")
    assert pages == ic.render_pages(domains, fp, "control-plane/invariants")
    assert [p["path"] for p in pages] == ["control-plane/invariants/example-observability.md"]
    body = pages[0]["content"]
    assert fp[:16] in body and "Edit it there, never here." in body
    assert "**Lifecycle:** REFERENCE" in body
    # The heading text is the bare id: the site's own slug is the anchor, and
    # the summary table links to exactly that anchor.
    assert "\n### I-COVERAGE-DERIVED-1\n" in body
    assert "](#i-coverage-derived-1)" in body
    assert "{#" not in body
    assert "`Vps3NodeDown`" in body
    # Prose braces are entity-escaped for the redaction transform.
    assert "&#123;e.g. a handshake age&#125;" in body


def test_heading_anchor_matches_the_site_slugger(tmp_path):
    markdown = pytest.importorskip("markdown")
    domains = _load(tmp_path)
    html = markdown.markdown(ic.render_pages(domains, ic.fingerprint(domains), "control-plane/invariants")[0]["content"],
                             extensions=["tables", "toc"])
    assert 'id="i-coverage-derived-1"' in html
    assert 'id="i-obs-liveness-1"' in html


def test_rendered_paths_and_nav_satisfy_the_deployed_contracts(tmp_path):
    from app.generator import _insert
    from app.publication import _PATH_RE

    domains = _load(tmp_path)
    pages = ic.render_pages(domains, ic.fingerprint(domains), "control-plane/invariants")
    tree: dict = {}
    for page in pages:
        assert _PATH_RE.fullmatch(page["path"]), page["path"]
        _insert(tree, page["nav_path"].split(" / "), page["path"])
    assert tree["Control Plane"]["Invariants"]["Example Observability invariants"] == pages[0]["path"]


def test_entity_attributes_satisfy_the_invariant_card_contract(tmp_path):
    from app.model_contracts import CARD_CONTRACTS, checklist_errors, secret_findings

    assert CARD_CONTRACTS["INVARIANT"]["ratified"] is True
    domains = _load(tmp_path)
    for domain, record in ic.iter_records(domains):
        attributes = ic.record_attributes(domain, domains[domain], record, "control-plane/invariants")
        assert checklist_errors("INVARIANT", attributes) == []
        assert secret_findings(attributes) == []
        assert attributes["owner"] == "example-observability"
        assert attributes["source_page_path"] == "control-plane/invariants/example-observability.md"
    assert checklist_errors("INVARIANT", {"invariant_id": "I-X-1"})  # the checklist bites


# ── Lifecycle reconciliation against a fake model API ───────────────────────

class FakeModelClient:
    def __init__(self, pages: dict[str, dict] | None = None):
        self.entities: dict[str, dict] = {}
        self.pages = pages or {}
        self.counter = 0
        self.calls: list[tuple[str, str]] = []

    def call(self, method: str, path: str, body: dict | None = None, key: str | None = None) -> dict:
        self.calls.append((method, path))
        if method == "GET" and path.startswith("/api/v1/pages?"):
            wanted = path.split("path=", 1)[1].split("&", 1)[0]
            return {"pages": [self.pages[wanted]] if wanted in self.pages else []}
        if method == "GET":
            params = dict(pair.split("=") for pair in path.split("?", 1)[1].split("&"))
            status = params.get("status", "ACTIVE")
            return {"entities": [dict(e) for e in self.entities.values()
                                 if e["entity_kind"] == params["entity_kind"] and (status == "all" or e["status"] == status)]}
        if path.endswith(("/update", "/retire", "/reactivate")):
            entity = self.entities[path.split("/")[-2]]
            assert body["expected_version"] == entity["version"], "stale CAS version"
            if path.endswith("/update"):
                entity.update(display_name=body["display_name"], attributes=body["attributes"])
            else:
                entity["status"] = "RETIRED" if path.endswith("/retire") else "ACTIVE"
            entity["version"] += 1
            return dict(entity)
        self.counter += 1
        entity = {"entity_id": f"ent-{self.counter:04d}", "entity_kind": body["entity_kind"], "entity_key": body["entity_key"],
                  "display_name": body["display_name"], "attributes": body.get("attributes", {}), "status": "ACTIVE", "version": 1}
        self.entities[entity["entity_id"]] = entity
        return dict(entity)

    def by_key(self, key: str) -> dict:
        return next(e for e in self.entities.values() if e["entity_key"] == key)


def _reconcile(fake, tmp_path, text=SOURCE, **kwargs):
    domains = ic.load_sources(_source(tmp_path, text))
    return ic.reconcile_entities(fake, domains, ic.fingerprint(domains), "control-plane/invariants", **kwargs)


def test_create_update_converge_retire_and_reactivate(tmp_path):
    fake = FakeModelClient()
    first = _reconcile(fake, tmp_path)
    assert first["created"] == 2
    assert fake.by_key("invariant-registry")["entity_kind"] == "SYSTEM"
    record = fake.by_key("i-coverage-derived-1")
    assert record["entity_kind"] == "INVARIANT"
    assert record["display_name"].startswith("I-COVERAGE-DERIVED-1 — ")
    assert set(first["active_pages"].values()) == {"control-plane/invariants/example-observability.md"}

    second = _reconcile(fake, tmp_path, SOURCE.replace("enforcement: PARTIAL", "enforcement: ENFORCED"))
    assert (second["created"], second["updated"]) == (0, 1)
    assert fake.by_key("i-coverage-derived-1")["attributes"]["enforcement"] == "ENFORCED"

    third = _reconcile(fake, tmp_path, SOURCE.replace("enforcement: PARTIAL", "enforcement: ENFORCED"))
    assert all(third[k] == 0 for k in ("created", "updated", "retired", "reactivated"))

    only_one = SOURCE.split("  - id: I-OBS-LIVENESS-1")[0]
    fourth = _reconcile(fake, tmp_path, only_one)
    assert fourth["retired"] == 1 and fake.by_key("i-obs-liveness-1")["status"] == "RETIRED"
    assert fake.by_key("i-obs-liveness-1")["entity_id"] in fourth["retired_ids"]

    fifth = _reconcile(fake, tmp_path)
    assert fifth["reactivated"] == 1 and fifth["created"] == 0
    assert fake.by_key("i-obs-liveness-1")["status"] == "ACTIVE"


def test_mass_retirement_is_refused_without_explicit_consent(tmp_path):
    fake = FakeModelClient()
    for index in range(6):
        fake.call("POST", "/api/v1/model/entities", {"entity_kind": "INVARIANT", "entity_key": f"i-old-{index}", "display_name": "old"})
    with pytest.raises(RuntimeError, match="refusing to retire"):
        _reconcile(fake, tmp_path)
    assert _reconcile(fake, tmp_path, allow_mass_retirement=True)["retired"] == 6


def test_catalogues_mapping_is_exact_and_empties_retired_records(tmp_path):
    reconciled = {"active_pages": {"ent-1": "a.md"}, "retired_ids": ["ent-2"]}
    assert ic.catalogues_mappings(reconciled, {"a.md": "page-a"}) == {"ent-1": ["page-a"], "ent-2": []}


def test_a_hand_authored_page_at_a_view_path_is_never_adopted(tmp_path):
    domains = _load(tmp_path)
    pages = ic.render_pages(domains, ic.fingerprint(domains), "control-plane/invariants")
    fake = FakeModelClient(pages={pages[0]["path"]: {"resource_id": "authored-1", "revision": "r1", "status": "active"}})
    with pytest.raises(RuntimeError, match="refusing to replace it"):
        ic.check_target_collisions(fake, pages, None)
    ic.check_target_collisions(fake, pages, {"target_page_paths": [pages[0]["path"]]})


# ── CLI ─────────────────────────────────────────────────────────────────────

def test_validate_only_and_dry_run_need_no_api(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INVARIANT_SOURCE_DIR", str(_source(tmp_path)))
    monkeypatch.delenv("DOCPLANE_API", raising=False)
    assert ic.main(["--validate-only"]) == 0
    assert "VALID 2 invariants across 1 domains" in capsys.readouterr().out
    assert ic.main(["--dry-run"]) == 0
    assert "DRY-RUN would publish control-plane/invariants/example-observability.md" in capsys.readouterr().out


def test_an_invalid_source_exits_nonzero_and_names_every_finding(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INVARIANT_SOURCE_DIR", str(_source(tmp_path, SOURCE.replace("criticality: IMPORTANT", "criticality: HIGH"))))
    assert ic.main(["--validate-only"]) == 1
    assert "INVALID" in capsys.readouterr().err
