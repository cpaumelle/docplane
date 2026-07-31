"""Sprint 6 exemplar: the monitoring meter-list importer.

Prometheus holds the readings; DocPlane holds the meter list. These tests
cover the pure core — parsing determinism, fingerprint sensitivity,
redaction-gated rendering, nav validity under the deployed validator, and
payload pinning against the deployed API contracts (every lesson the
Sprint 5 canary taught, applied before first fabric contact).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import meter_list  # noqa: E402

RULES_YML = """
groups:
- name: backup_tier1
  interval: 60s
  rules:
  - alert: BackupLegFailed
    expr: hub2_backup_leg_success == 0
    for: 15m
    labels:
      severity: warning
      service: backup
    annotations:
      summary: "Tier-1 backup leg failed on last run"
      description: |
        The most recent backup run reported failure.
  - alert: BackupStale
    expr: time() - hub2_backup_last_success_timestamp > 93600
    labels:
      severity: critical
      service: backup
    annotations:
      summary: "no successful copy in >26h"
- name: recording
  rules:
  - record: job:up:ratio
    expr: avg by (job) (up)
"""


def _rules_dir(tmp_path: Path) -> Path:
    (tmp_path / "backup-alerts.yml").write_text(RULES_YML, encoding="utf-8")
    return tmp_path


def test_parsing_is_deterministic_and_captures_the_meterable_fields(tmp_path):
    structure = meter_list.parse_rules(_rules_dir(tmp_path))
    assert structure == meter_list.parse_rules(tmp_path)
    rules = structure["backup-alerts"]["backup_tier1"]
    assert [rule["name"] for rule in rules] == ["BackupLegFailed", "BackupStale"]
    first = rules[0]
    assert first["rule_kind"] == "alert"
    assert first["service"] == "backup"
    assert first["severity"] == "warning"
    assert first["pending_for"] == "15m"
    assert first["description"]
    assert rules[1]["description"] is None
    assert structure["backup-alerts"]["recording"][0]["rule_kind"] == "record"


def test_fingerprint_changes_when_a_rule_changes(tmp_path):
    base = meter_list.fingerprint(meter_list.parse_rules(_rules_dir(tmp_path)))
    (tmp_path / "backup-alerts.yml").write_text(RULES_YML.replace("93600", "90000"), encoding="utf-8")
    assert meter_list.fingerprint(meter_list.parse_rules(tmp_path)) != base
    assert len(base) == 64


def test_rendering_is_deterministic_stamped_and_flags_description_gaps(tmp_path):
    structure = meter_list.parse_rules(_rules_dir(tmp_path))
    fp = meter_list.fingerprint(structure)
    pages = meter_list.render_pages("hub2.prometheus", structure, fp)
    assert pages == meter_list.render_pages("hub2.prometheus", structure, fp)
    assert [page["path"] for page in pages] == [
        "observe/meter-list/hub2-prometheus/index.md",
        "observe/meter-list/hub2-prometheus/backup-alerts.md",
    ]
    body = pages[1]["content"]
    assert fp[:16] in body
    assert "```promql" in body
    # Gaps, never stubs: a rule without a description says so explicitly.
    assert "surfaced as a coverage gap" in body
    assert "watches `backup`" in body


def test_rendering_is_redaction_gated(tmp_path):
    poisoned = RULES_YML.replace(
        'summary: "no successful copy in >26h"',
        'summary: "token AKIAIOSFODNN7REALKEY leaked into an annotation"',
    )
    (tmp_path / "backup-alerts.yml").write_text(poisoned, encoding="utf-8")
    structure = meter_list.parse_rules(tmp_path)
    pages = meter_list.render_pages("hub2.prometheus", structure, meter_list.fingerprint(structure))
    rendered = "\n".join(page["content"] for page in pages)
    assert "AKIAIOSFODNN7REALKEY" not in rendered


def test_unbalanced_brace_prose_survives_the_redaction_invariant(tmp_path):
    """A real hub2 rule's description contains grep 'metric{' — legitimately
    unbalanced braces that the canonical transform's brace invariant refuses.
    Prose braces are entity-escaped so the document stays brace-free for the
    transform while rendering identically."""
    poisoned = RULES_YML.replace(
        "The most recent backup run reported failure.",
        "Inspect: ssh px1 \"grep 'ceph_tuning_drift{' /var/lib/prom/file.prom\"",
    )
    (tmp_path / "backup-alerts.yml").write_text(poisoned, encoding="utf-8")
    structure = meter_list.parse_rules(tmp_path)
    pages = meter_list.render_pages("hub2.prometheus", structure, meter_list.fingerprint(structure))
    body = pages[1]["content"]
    assert "ceph_tuning_drift&#123;" in body
    assert "grep 'ceph_tuning_drift{'" not in body
    # PromQL fences keep their braces verbatim.
    assert "hub2_backup_leg_success == 0" in body


def test_navigation_is_collision_free_under_the_deployed_validator(tmp_path):
    from app.generator import _insert

    structure = meter_list.parse_rules(_rules_dir(tmp_path))
    fp = meter_list.fingerprint(structure)
    tree: dict = {}
    for page in [meter_list.presence_page(), *meter_list.render_pages("hub2.prometheus", structure, fp)]:
        _insert(tree, page["nav_path"].split(" / "), page["path"])
    # Nav keeps the true dotted identity for humans; paths carry the slug.
    source_node = tree["Observe"]["Meter list"]["hub2.prometheus"]
    assert isinstance(source_node, dict)
    assert source_node["Overview"] == "observe/meter-list/hub2-prometheus/index.md"
    assert tree["Observe"]["Meter list"]["Overview"] == "observe/meter-list/index.md"


def test_every_rendered_path_satisfies_the_deployed_path_contract(tmp_path):
    """The Sprint 6 fabric canary lesson: nav was validated under the
    deployed validator, but page PATHS never were — and the deployed path
    contract forbids dots outside the .md suffix, refusing all 36 pages of
    the first live run (source key hub2.prometheus). Validate every
    rendered path against the real publication regex, with the exact
    dotted source key the fabric uses AND a dotted file stem."""
    from app.publication import _PATH_RE

    (tmp_path / "ceph.rules.yml").write_text(RULES_YML, encoding="utf-8")
    structure = meter_list.parse_rules(_rules_dir(tmp_path))
    fp = meter_list.fingerprint(structure)
    pages = meter_list.render_pages("hub2.prometheus", structure, fp)
    for page in [meter_list.presence_page(), *pages]:
        assert _PATH_RE.fullmatch(page["path"]), page["path"]
    # The dotted identities survive where they belong: entity keys and nav.
    assert meter_list._slug("hub2.prometheus") == "hub2.prometheus"
    assert {page["path"] for page in pages} == {
        "observe/meter-list/hub2-prometheus/index.md",
        "observe/meter-list/hub2-prometheus/backup-alerts.md",
        "observe/meter-list/hub2-prometheus/ceph-rules.md",
    }
    # Index links point at the slugged paths, relative to the source dir.
    index = pages[0]["content"]
    assert "(hub2-prometheus/ceph-rules.md)" in index
    assert "`ceph.rules`" in index


def test_colliding_file_stem_slugs_are_refused(tmp_path):
    (tmp_path / "foo.rules.yml").write_text(RULES_YML, encoding="utf-8")
    (tmp_path / "foo-rules.yml").write_text(RULES_YML, encoding="utf-8")
    structure = meter_list.parse_rules(tmp_path)
    with pytest.raises(RuntimeError, match="both slug to page path segment"):
        meter_list.render_pages("hub2.prometheus", structure, meter_list.fingerprint(structure))


def test_importer_payloads_validate_against_the_deployed_api_models(tmp_path):
    from uuid import uuid4

    from app.model_models import ArtifactDeclare, EntityCreate, EntityLinkCreate, EntityLinkRemove, EntityRetire, EntityUpdate
    from app.observe_models import ObservationBatch

    structure = meter_list.parse_rules(_rules_dir(tmp_path))
    fp = meter_list.fingerprint(structure)
    for file_stem, group_name, rule in meter_list.iter_rules(structure):
        attributes = meter_list.rule_attributes(file_stem, group_name, rule)
        EntityCreate.model_validate(
            {"entity_kind": "MONITOR_RULE", "entity_key": f"rule.{meter_list._slug(rule['name'])}", "display_name": rule["name"], "attributes": attributes}
        )
        EntityUpdate.model_validate({"expected_version": 1, "display_name": rule["name"], "attributes": attributes})
    EntityCreate.model_validate({"entity_kind": "SERVICE", "entity_key": "backup", "display_name": "backup"})
    EntityLinkCreate.model_validate({"relation": "WATCHES", "to_entity_id": uuid4()})
    EntityLinkRemove.model_validate({"relation": "WATCHES", "to_entity_id": uuid4(), "note": "service label moved"})
    EntityRetire.model_validate({"expected_version": 2, "note": "absent from rule set"})
    ArtifactDeclare.model_validate(
        {
            "artifact_key": "meter-list-hub2.prometheus",
            "generator_name": meter_list.GENERATOR_NAME,
            "generator_version": meter_list.GENERATOR_VERSION,
            "source_entity_id": uuid4(),
            "target_page_resource_ids": [uuid4()],
        }
    )
    ObservationBatch.model_validate(
        {
            "observations": [
                {
                    "subject_artifact_id": uuid4(),
                    "observation_kind": "GENERATION",
                    "source_fingerprint": fp,
                    "summary": "Imported rules",
                    "idempotency_key": meter_list._key(fp, "generation"),
                }
            ]
        }
    )


def test_idempotency_keys_are_versioned_and_fingerprint_bound():
    fp = "ab" * 32
    key = meter_list._key(fp, "operation", "observe/meter-list/hub2.prometheus/backup-alerts.md")
    assert key.startswith(f"meter-list-{meter_list.GENERATOR_VERSION}-{fp[:16]}-operation-")
    assert len(key) <= 256
    assert key != meter_list._key("cd" * 32, "operation", "observe/meter-list/hub2.prometheus/backup-alerts.md")


def test_slug_is_stable_and_charset_safe():
    assert meter_list._slug("Hub2BackupLegFailed") == "hub2backuplegfailed"
    assert meter_list._slug("job:up:ratio") == "job-up-ratio"
    assert meter_list._slug("  Weird -- Name!!  ") == "weird----name"
    import re

    assert re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,126}", "rule." + meter_list._slug("job:up:ratio"))


def test_runbook_url_is_harvested_and_rendered(tmp_path):
    with_runbook = RULES_YML.replace(
        'summary: "no successful copy in >26h"',
        'summary: "no successful copy in >26h"\n      runbook_url: https://docs.example/runbooks/backup',
    )
    (tmp_path / "backup-alerts.yml").write_text(with_runbook, encoding="utf-8")
    structure = meter_list.parse_rules(tmp_path)
    stale = structure["backup-alerts"]["backup_tier1"][1]
    assert stale["runbook_url"] == "https://docs.example/runbooks/backup"
    assert meter_list.rule_attributes("backup-alerts", "backup_tier1", stale)["runbook_url"] == "https://docs.example/runbooks/backup"
    body = meter_list.render_pages("hub2.prometheus", structure, meter_list.fingerprint(structure))[1]["content"]
    assert "**Runbook**: <https://docs.example/runbooks/backup>" in body


# ── Lifecycle reconciliation against a fake model API ───────────────────────

class FakeModelClient:
    """In-memory stand-in for the model-API surface reconcile_entities
    drives, faithful to the deployed semantics the e2e suite proves:
    ACTIVE-filtered listings, versioned CAS on update/retire, idempotent
    link add/remove keyed by (from, relation, to)."""

    def __init__(self):
        self.entities: dict[str, dict] = {}
        self.links: dict[tuple[str, str, str], dict] = {}
        self.counter = 0

    def call(self, method: str, path: str, body: dict | None = None, key: str | None = None) -> dict:
        if method == "GET" and "?" in path:
            params = dict(pair.split("=") for pair in path.split("?", 1)[1].split("&"))
            status = params.get("status", "ACTIVE")
            return {
                "entities": [
                    dict(e) for e in self.entities.values()
                    if e["entity_kind"] == params["entity_kind"] and (status == "all" or e["status"] == status)
                ]
            }
        if method == "GET":
            entity = self.entities[path.rsplit("/", 1)[-1]]
            links_out = [
                {"relation": relation, "to_entity_id": to, "entity_key": self.entities[to]["entity_key"], "metadata": metadata}
                for (source, relation, to), metadata in sorted(self.links.items())
                if source == entity["entity_id"]
            ]
            return {**entity, "links_out": links_out}
        if path.endswith("/links/remove"):
            self.links.pop((path.split("/")[-3], body["relation"], body["to_entity_id"]), None)
            return {"removed": True}
        if path.endswith("/links"):
            self.links[(path.split("/")[-2], body["relation"], body["to_entity_id"])] = body.get("metadata", {})
            return {}
        if path.endswith("/update"):
            entity = self.entities[path.split("/")[-2]]
            assert body["expected_version"] == entity["version"], "stale CAS version"
            entity.update(display_name=body["display_name"], attributes=body["attributes"], version=entity["version"] + 1)
            return dict(entity)
        if path.endswith("/retire"):
            entity = self.entities[path.split("/")[-2]]
            assert body["expected_version"] == entity["version"], "stale CAS version"
            entity.update(status="RETIRED", version=entity["version"] + 1)
            return dict(entity)
        if path.endswith("/reactivate"):
            entity = self.entities[path.split("/")[-2]]
            assert body["expected_version"] == entity["version"], "stale CAS version"
            entity.update(status="ACTIVE", version=entity["version"] + 1)
            return dict(entity)
        self.counter += 1
        entity = {
            "entity_id": f"ent-{self.counter:04d}", "entity_kind": body["entity_kind"],
            "entity_key": body["entity_key"], "display_name": body["display_name"],
            "attributes": body.get("attributes", {}), "status": "ACTIVE", "version": 1,
        }
        self.entities[entity["entity_id"]] = entity
        return dict(entity)

    def by_key(self, entity_key: str) -> dict:
        return next(e for e in self.entities.values() if e["entity_key"] == entity_key)

    def watches(self, entity_key: str) -> list[str]:
        rule_id = self.by_key(entity_key)["entity_id"]
        return sorted(self.entities[to]["entity_key"] for (source, relation, to) in self.links if source == rule_id and relation == "WATCHES")


def _reconcile(fake: FakeModelClient, tmp_path: Path, yml: str, **kwargs) -> dict:
    (tmp_path / "backup-alerts.yml").write_text(yml, encoding="utf-8")
    structure = meter_list.parse_rules(tmp_path)
    return meter_list.reconcile_entities(fake, "hub2.prometheus", structure, meter_list.fingerprint(structure), **kwargs)


def test_second_run_updates_edited_rules_and_replaces_stale_watches(tmp_path):
    fake = FakeModelClient()
    first = _reconcile(fake, tmp_path, RULES_YML)
    assert first["created"] == 3 and first["links_added"] == 2

    edited = RULES_YML.replace(
        "severity: critical\n      service: backup",
        "severity: warning\n      service: backup2",
    )
    second = _reconcile(fake, tmp_path, edited)
    assert second["created"] == 0 and second["retired"] == 0
    assert second["updated"] == 1
    entity = fake.by_key("rule.backupstale")
    # The entity mirrors the edited rule file, same identity, bumped version.
    assert entity["attributes"]["severity"] == "warning"
    assert entity["version"] == 2
    # The WATCHES wire followed the service label: old edge gone, new added.
    assert second["links_removed"] == 1 and second["links_added"] == 1
    assert fake.watches("rule.backupstale") == ["backup2"]

    # Convergence: a third run over the same rule set is a graph no-op.
    third = _reconcile(fake, tmp_path, edited)
    assert all(third[key] == 0 for key in ("created", "updated", "retired", "reactivated", "links_added", "links_updated", "links_removed"))


def test_upstream_service_label_wiring_remains_incremental(tmp_path):
    fake = FakeModelClient()
    _reconcile(fake, tmp_path, RULES_YML)
    second = _reconcile(fake, tmp_path, RULES_YML)
    assert second["links_added"] == second["links_removed"] == 0
    rule_id = fake.by_key("rule.backuplegfailed")["entity_id"]
    metadata = next(value for (source, relation, _), value in fake.links.items() if source == rule_id and relation == "WATCHES")
    assert metadata == {"source": "rule_label"}


def test_overlay_wires_only_unlabelled_rules_with_provenance(tmp_path):
    fake = FakeModelClient()
    fake.call("POST", "/api/v1/model/entities", {
        "entity_kind": "SERVICE", "entity_key": "prometheus", "display_name": "Prometheus",
    })
    summary = _reconcile(
        fake, tmp_path, RULES_YML,
        service_overlay=[{"pattern": "job:*", "service_entity_key": "prometheus"}],
    )
    assert summary["warnings"] == []
    assert fake.watches("rule.job-up-ratio") == ["prometheus"]
    rule_id = fake.by_key("rule.job-up-ratio")["entity_id"]
    metadata = next(value for (source, relation, _), value in fake.links.items() if source == rule_id and relation == "WATCHES")
    assert metadata == {"source": "overlay"}
    converged = _reconcile(
        fake, tmp_path, RULES_YML,
        service_overlay=[{"pattern": "job:*", "service_entity_key": "prometheus"}],
    )
    assert all(converged[key] == 0 for key in ("created", "updated", "retired", "reactivated", "links_added", "links_updated", "links_removed"))


def test_overlay_unknown_services_and_zero_matches_are_loud(tmp_path):
    structure = meter_list.parse_rules(_rules_dir(tmp_path))
    assignments, warnings = meter_list.overlay_assignments(
        structure,
        [
            {"pattern": "DoesNotExist", "service_entity_key": "backup"},
            {"pattern": "job:*", "service_entity_key": "unknown"},
        ],
        {"backup"},
    )
    assert assignments == {}
    assert any("matched zero" in item for item in warnings)
    assert any("unknown SERVICE" in item for item in warnings)


def test_suggestions_are_names_only_and_never_mappings(tmp_path):
    report = meter_list.service_suggestions(
        meter_list.parse_rules(_rules_dir(tmp_path)),
        ["backup", "prometheus"],
    )
    assert report["count"] == 1
    assert report["rules"][0]["rule_name"] == "job:up:ratio"
    assert "expr" not in json.dumps(report)


def test_rules_removed_from_git_are_retired(tmp_path):
    fake = FakeModelClient()
    _reconcile(fake, tmp_path, RULES_YML)
    removed = RULES_YML.replace(
        """  - alert: BackupStale
    expr: time() - hub2_backup_last_success_timestamp > 93600
    labels:
      severity: critical
      service: backup
    annotations:
      summary: "no successful copy in >26h"
""",
        "",
    )
    summary = _reconcile(fake, tmp_path, removed)
    assert summary["retired"] == 1
    assert fake.by_key("rule.backupstale")["status"] == "RETIRED"
    # Retirement converges too — the second pass has nothing left to do.
    assert _reconcile(fake, tmp_path, removed)["retired"] == 0
    # A rule restored in git resumes its identity — (kind, key) is unique
    # across statuses, so resurrection must never mint a duplicate.
    restored = _reconcile(fake, tmp_path, RULES_YML)
    assert restored["reactivated"] == 1 and restored["created"] == 0
    assert fake.by_key("rule.backupstale")["status"] == "ACTIVE"


def test_mass_retirement_is_bounded_and_operator_overridable(tmp_path):
    fake = FakeModelClient()
    body = "\n".join(f"  - alert: Rule{i}\n    expr: up == {i}" for i in range(7))
    _reconcile(fake, tmp_path, f"groups:\n- name: g\n  rules:\n{body}\n")
    shrunk = "groups:\n- name: g\n  rules:\n  - alert: Rule0\n    expr: up == 0\n"
    with pytest.raises(RuntimeError, match="--allow-mass-retirement"):
        _reconcile(fake, tmp_path, shrunk)
    assert _reconcile(fake, tmp_path, shrunk, allow_mass_retirement=True)["retired"] == 6


def test_artifact_succession_triggers_on_target_or_contract_change():
    artifact = {"target_page_paths": ["a.md", "b.md"], "generator_version": meter_list.GENERATOR_VERSION}
    assert not meter_list.artifact_needs_succession(artifact, ["b.md", "a.md"])
    assert meter_list.artifact_needs_succession(artifact, ["a.md"])
    assert meter_list.artifact_needs_succession(artifact, ["a.md", "b.md", "c.md"])
    assert meter_list.artifact_needs_succession({**artifact, "generator_version": "1.0.0"}, ["a.md", "b.md"])


def test_scheduled_unchanged_run_does_not_write_work():
    assert not meter_list.should_reconcile_gap_work(source_changed=False, explicit=False)
    assert meter_list.should_reconcile_gap_work(source_changed=True, explicit=False)
    assert meter_list.should_reconcile_gap_work(source_changed=False, explicit=True)


def test_unchanged_main_replays_nominal_observation_but_never_writes_work(tmp_path, monkeypatch, capsys):
    rules_dir = _rules_dir(tmp_path)
    structure = meter_list.parse_rules(rules_dir)
    fp = meter_list.fingerprint(structure)
    paths = sorted(page["path"] for page in meter_list.render_pages("hub2.prometheus", structure, fp))
    calls = []

    class RecordingClient:
        def __init__(self, *args):
            pass

        def call(self, method, path, body=None, key=None):
            calls.append((method, path, body, key))
            return {"recorded": [{"replayed": True}]}

    import schema_catalogue as sc

    monkeypatch.setattr(meter_list, "Client", RecordingClient)
    monkeypatch.setattr(
        meter_list,
        "reconcile_entities",
        lambda *args, **kwargs: {
            "source_id": "source", "created": 0, "updated": 0, "retired": 0,
            "reactivated": 0, "links_added": 0, "links_updated": 0,
            "links_removed": 0, "warnings": [],
        },
    )
    monkeypatch.setattr(
        sc,
        "current_artifact",
        lambda *args: {
            "artifact_id": "artifact", "generator_version": meter_list.GENERATOR_VERSION,
            "target_page_paths": paths,
        },
    )
    monkeypatch.setattr(sc, "last_generation_fingerprint", lambda *args: fp)
    monkeypatch.setenv("METER_RULES_DIR", str(rules_dir))
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_METER_LIST_TOKEN", "not-printed")

    assert meter_list.main([]) == 0
    assert "UNCHANGED" in capsys.readouterr().out
    assert [path for _, path, _, _ in calls] == ["/api/v1/observations"]
    assert all("/work" not in path for _, path, _, _ in calls)
