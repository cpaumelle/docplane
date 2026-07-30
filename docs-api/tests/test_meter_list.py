"""Sprint 6 exemplar: the monitoring meter-list importer.

Prometheus holds the readings; DocPlane holds the meter list. These tests
cover the pure core — parsing determinism, fingerprint sensitivity,
redaction-gated rendering, nav validity under the deployed validator, and
payload pinning against the deployed API contracts (every lesson the
Sprint 5 canary taught, applied before first fabric contact).
"""
from __future__ import annotations

import sys
from pathlib import Path

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
        "observe/meter-list/hub2.prometheus/index.md",
        "observe/meter-list/hub2.prometheus/backup-alerts.md",
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
    source_node = tree["Observe"]["Meter list"]["hub2.prometheus"]
    assert isinstance(source_node, dict)
    assert source_node["Overview"] == "observe/meter-list/hub2.prometheus/index.md"
    assert tree["Observe"]["Meter list"]["Overview"] == "observe/meter-list/index.md"


def test_importer_payloads_validate_against_the_deployed_api_models(tmp_path):
    from uuid import uuid4

    from app.model_models import ArtifactDeclare, EntityCreate, EntityLinkCreate
    from app.observe_models import ObservationBatch

    structure = meter_list.parse_rules(_rules_dir(tmp_path))
    fp = meter_list.fingerprint(structure)
    for file_stem, group_name, rule in meter_list.iter_rules(structure):
        attributes = {
            "rule_kind": rule["rule_kind"], "expr": rule["expr"],
            "source_file": f"{file_stem}.yml", "group": group_name,
            "has_description": bool(rule.get("description")),
        }
        EntityCreate.model_validate(
            {"entity_kind": "MONITOR_RULE", "entity_key": f"rule.{meter_list._slug(rule['name'])}", "display_name": rule["name"], "attributes": attributes}
        )
    EntityCreate.model_validate({"entity_kind": "SERVICE", "entity_key": "backup", "display_name": "backup"})
    EntityLinkCreate.model_validate({"relation": "WATCHES", "to_entity_id": uuid4()})
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
