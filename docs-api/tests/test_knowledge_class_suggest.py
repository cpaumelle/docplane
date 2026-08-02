"""Knowledge-class suggestion tool: names-only, deterministic, write-free.

Contract under test (Sprint 10 precursor; issue #64 answer 2 sequences
audit → backfill → CHECK):
- Signal precedence: legacy inline lifecycle > content shape > path prefix.
- Legacy mapping: REFERENCE/OPERATION direct; GTD work-states → WORK_NOTE;
  ARCHIVED proposes nothing (archive state is not a knowledge class).
- Content shapes: ADR anatomy → DECISION; runbook contract headings →
  OPERATION; invariants register → POLICY.
- Classified pages are skipped, but a tier-1 legacy field disagreeing with
  the stored class lands in `conflicts`.
- Report is sorted, counted, contract-versioned, and proposes only — every
  page is either classified, suggested, or listed in no_signal.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from knowledge_class_suggest import (  # noqa: E402
    CONTRACT_VERSION,
    build_report,
    conflict_for,
    propose,
)


def _page(path="guides/x.md", title="X", content="body", knowledge_class=None, **extra):
    return {"path": path, "title": title, "content": content,
            "knowledge_class": knowledge_class, "revision": "r1", **extra}


def test_legacy_lifecycle_beats_everything():
    proposal = propose(
        "operations/backup.md", "Backup",
        "# Backup\n\n**Lifecycle:** REFERENCE\n\n## Preconditions\n## Procedure\n## Rollback\n",
    )
    assert proposal["proposed"] == "REFERENCE"
    assert proposal["signal"] == "legacy-lifecycle"
    assert proposal["confidence"] == 0.9


def test_legacy_work_states_map_to_work_note_and_archived_maps_to_nothing():
    active = propose("notes/todo.md", "Todo", "<!-- lifecycle: active -->\n")
    assert active["proposed"] == "WORK_NOTE"
    archived = propose("archive/old.md", "Old", "**Lifecycle:** ARCHIVED\n")
    assert archived is None or archived["signal"] != "legacy-lifecycle"


def test_content_shapes():
    adr = propose("decisions/adr-031.md", "ADR-031 Edge relocation",
                  "# ADR-031\n\n## Context\n…\n## Decision\n…\n## Consequences\n…")
    assert (adr["proposed"], adr["signal"]) == ("DECISION", "content-shape")
    runbook = propose("misc/fix.md", "Fix",
                      "# Fix\n\n## Preconditions\n## Procedure\n## Rollback\n")
    assert runbook["proposed"] == "OPERATION"
    register = propose("misc/rules.md", "Rules", "INV-7 must hold.\nINV-9 also.\n")
    assert register["proposed"] == "POLICY"


def test_path_prefix_is_the_fallback():
    assert propose("reference/api.md", "API", "plain")["proposed"] == "REFERENCE"
    assert propose("sites/example-town.md", "Example Town", "plain")["confidence"] == 0.5
    assert propose("uncharted/thing.md", "Thing", "plain") is None


def test_conflicts_surface_disagreeing_legacy_fields_only():
    disagree = conflict_for("sites/example-town.md", "REFERENCE", "**Lifecycle:** OPERATION\n")
    assert disagree["implied"] == "OPERATION" and disagree["current"] == "REFERENCE"
    assert conflict_for("sites/example-town.md", "OPERATION", "**Lifecycle:** OPERATION\n") is None
    assert conflict_for("sites/example-town.md", "REFERENCE", "no field") is None


def test_report_is_total_sorted_and_write_free():
    pages = [
        _page(path="z/unknown.md"),
        _page(path="operations/backup.md", content="**Lifecycle:** OPERATION"),
        _page(path="sites/example-town.md", knowledge_class="REFERENCE",
              content="**Lifecycle:** OPERATION"),
        _page(path="reference/api.md", knowledge_class="REFERENCE", content="fine"),
    ]
    snapshot = [dict(page) for page in pages]
    report = build_report(pages)
    assert report["contract_version"] == CONTRACT_VERSION
    counts = report["counts"]
    assert counts == {"pages": 4, "classified": 2, "missing": 2,
                      "proposed": 1, "no_signal": 1, "conflicts": 1}
    # total accounting: every page is classified, suggested, or no_signal
    assert counts["classified"] + counts["proposed"] + counts["no_signal"] == counts["pages"]
    assert [item["path"] for item in report["suggestions"]] == ["operations/backup.md"]
    assert report["no_signal"] == ["z/unknown.md"]
    assert report["conflicts"][0]["path"] == "sites/example-town.md"
    assert pages == snapshot  # proposes only — never mutates its input
    assert build_report(pages) == report  # deterministic


def test_api_report_carries_metadata_version_for_optimistic_apply():
    report = build_report([_page(
        path="operations/backup.md",
        resource_id="page-1",
        metadata_version=7,
    )])
    assert report["suggestions"][0]["metadata_version"] == 7
