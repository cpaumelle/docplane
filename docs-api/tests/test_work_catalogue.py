"""The work catalogue: a browsable, read-only work/ section generated from
initiative state. Tests cover the pure core — projection determinism,
fingerprint sensitivity, the inbox-count-only rule, queue and initiative
rendering — plus the UNCHANGED main path never publishing."""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import work_catalogue  # noqa: E402
from migration.redaction import DocumentRefusedError, MalformedMarkerError, redact  # noqa: E402


def _initiative(**overrides):
    base = {
        "initiative_id": "00000000-0000-0000-0000-000000000001",
        "initiative_key": "example-upgrade",
        "title": "Upgrade the example service",
        "objective": "Move the example service to the new runtime.",
        "work_state": "BACKLOG",
        "priority": "HIGH",
        "target_date": None,
        "review_due_at": None,
        "blocker_summary": None,
        "soak_started_at": None,
        "soak_review_at": None,
        "soak_success_criteria": None,
        "soak_failure_conditions": None,
        "soak_monitoring_ref": None,
        "parked_reason": None,
        "parked_review_at": None,
        "parked_indefinitely": False,
        "promotion_state": "UPDATED",
        "model_disposition": "NOT_REQUIRED",
        "observe_disposition": "DEFERRED",
        "completed_at": None,
    }
    base.update(overrides)
    return base


def _state(initiatives, details=None, inbox=3, wip=2):
    return {
        "wip_limit": wip,
        "inbox_count": inbox,
        "initiatives": initiatives,
        "details": details or {},
    }


def test_fingerprint_is_deterministic_and_order_insensitive():
    a = _initiative()
    b = _initiative(initiative_id="00000000-0000-0000-0000-000000000002",
                    initiative_key="another", title="Another", work_state="ACTIVE")
    assert work_catalogue.fingerprint(_state([a, b])) == work_catalogue.fingerprint(_state([b, a]))
    changed = _initiative(priority="LOW")
    assert work_catalogue.fingerprint(_state([a])) != work_catalogue.fingerprint(_state([changed]))


def test_inbox_captures_surface_as_count_only():
    state = _state([_initiative()], inbox=7)
    fp = work_catalogue.fingerprint(state)
    index = next(p for p in work_catalogue.render_pages(state, fp) if p["path"] == "work/index.md")
    assert "**7** untriaged capture" in index["content"]
    # No capture body text ever renders — the projection carries only a count.
    assert "captures" not in work_catalogue._projection(state) or isinstance(
        work_catalogue._projection(state).get("inbox_count"), int
    )


@pytest.fixture
def steady_state(monkeypatch):
    """Contract 2 after phase C: no migration coexistence."""
    monkeypatch.setattr(work_catalogue, "LEGACY_COEXISTENCE", False)


def _page(state):
    pages = {page["path"]: page for page in work_catalogue.render_pages(state, work_catalogue.fingerprint(state))}
    return pages["work/index.md"]["content"]


def test_one_catalogue_page_renders_every_queue_and_ranks_by_priority():
    rows = [
        _initiative(initiative_key="low", title="Low", priority="LOW"),
        _initiative(initiative_id="00000000-0000-0000-0000-000000000002",
                    initiative_key="crit", title="Crit", priority="CRITICAL"),
        _initiative(initiative_id="00000000-0000-0000-0000-000000000003",
                    initiative_key="soaker", title="Soaker", work_state="SOAKING",
                    soak_started_at="2026-07-20T00:00:00Z", soak_review_at="2026-08-03T00:00:00Z",
                    soak_success_criteria="No alert fires for 14 days",
                    soak_monitoring_ref="alert:ExampleServiceDown"),
        _initiative(initiative_id="00000000-0000-0000-0000-000000000004",
                    initiative_key="stuck", title="Stuck", work_state="BLOCKED",
                    blocker_summary="waiting on vendor firmware"),
        _initiative(initiative_id="00000000-0000-0000-0000-000000000005",
                    initiative_key="someday", title="Someday", work_state="PARKED",
                    parked_reason="after the move", parked_indefinitely=True),
    ]
    content = _page(_state(rows))
    for heading in ("## Now", "## Roadmap", "## Blocked", "## Soaking", "## Parked", "## Recently completed"):
        assert f"\n{heading}\n" in content
    # queue anchors replace the retired queue pages
    for anchor in ("#now", "#roadmap", "#blocked", "#soaking", "#parked", "#recently-completed"):
        assert f"]({anchor})" in content
    roadmap = content.split("## Roadmap", 1)[1].split("## Blocked", 1)[0]
    assert roadmap.index("### crit") < roadmap.index("### low")
    assert "Blocked on: waiting on vendor firmware" in content
    assert "No alert fires for 14 days" in content and "alert:ExampleServiceDown" in content
    assert "Parked (indefinitely): after the move" in content
    # Every open initiative is one entry whose bare-key heading is its anchor,
    # carrying the UUID that docplane_work_get takes.
    for key, uuid in (("crit", "00000000-0000-0000-0000-000000000002"),
                      ("stuck", "00000000-0000-0000-0000-000000000004")):
        assert f"\n### {key}\n" in content
        assert f"](#{key})" in content
        assert f"- ID: `{uuid}`" in content
    assert "initiatives/" not in content
    assert "/dashboard/#work" in content
    assert "docplane_work_get" in content


def test_page_cardinality_is_independent_of_initiative_count(steady_state):
    many = [
        _initiative(initiative_id=f"00000000-0000-0000-0000-{n:012d}", initiative_key=f"i-{n}",
                    title=f"I {n}", work_state=("ACTIVE", "BACKLOG", "BLOCKED", "SOAKING", "PARKED")[n % 5])
        for n in range(60)
    ]
    assert work_catalogue.desired_page_paths(_state(many)) == ["work/index.md"]
    assert work_catalogue.desired_page_paths(_state([])) == ["work/index.md"]
    assert len(work_catalogue.render_pages(_state(many), "f" * 64)) == 1


def test_entry_shows_count_and_a_bounded_preview_of_the_newest_activities_only():
    initiative = _initiative(work_state="ACTIVE")
    activities = [
        {"activity_type": "NOTE", "body": f"body-{n} " + "x" * 400, "created_at": f"2026-09-{n + 1:02d}T00:00:00Z"}
        for n in range(6)
    ]
    activities[-1]["body"] = (
        "see [the old page](work/initiatives/other-thing.md) and\n```\ncode\n```\ndone"
    )
    state = _state([initiative], details={initiative["initiative_id"]: {"activities": activities, "links": []}})
    content = _page(state)
    assert "Activity: 6 recorded, latest 3:" in content
    for older in ("body-0", "body-1", "body-2"):
        assert older not in content
    assert "body-3" in content and "body-4" in content
    # previews are one line, cut, and never re-publish recorded links or fences
    assert "x" * 300 not in content and "…" in content
    assert "see the old page and code done" in content
    assert "initiatives/other-thing.md" not in content and "```" not in content


def test_projection_carries_only_what_renders():
    initiative = _initiative(work_state="ACTIVE")
    activities = [{"activity_type": "NOTE", "body": f"b{n}", "created_at": "2026-09-01T00:00:00Z"} for n in range(5)]
    row = work_catalogue._projection(
        _state([initiative], details={initiative["initiative_id"]: {"activities": activities}})
    )["initiatives"][0]
    assert row["id"] == initiative["initiative_id"]
    assert row["activity_count"] == 5
    assert [a["preview"] for a in row["activities"]] == ["b2", "b3", "b4"]


def test_initiative_text_cannot_break_the_anchor_structure_and_old_links_resolve():
    objective = "Intro\n## Not a catalogue section\nSee [peer](../work/initiatives/peer-1.md#x)."
    content = _page(_state([_initiative(work_state="ACTIVE", objective=objective)]))
    assert "\n## Not a catalogue section" not in content
    assert "\\## Not a catalogue section" in content
    assert "[peer](#peer-1)" in content


def test_completed_initiatives_show_closure_gate_and_get_no_entry():
    done = _initiative(initiative_key="shipped", title="Shipped", work_state="COMPLETE",
                       completed_at="2026-07-30T12:00:00Z")
    content = _page(_state([done]))
    completed = content.split("## Recently completed", 1)[1]
    assert "| shipped | Shipped | 2026-07-30 | UPDATED / NOT_REQUIRED / DEFERRED |" in completed
    assert "### shipped" not in content


def test_abandoned_initiative_leaves_the_catalogue_without_changing_the_page_set(steady_state):
    active = _initiative(initiative_key="retired-plan", title="Retired plan", work_state="ACTIVE")
    before = _state([active])
    assert "### retired-plan" in _page(before)

    abandoned = _initiative(initiative_key="retired-plan", title="Retired plan", work_state="ABANDONED")
    after = _state([abandoned])
    assert "retired-plan" not in _page(after)
    assert work_catalogue.desired_page_paths(before) == work_catalogue.desired_page_paths(after)
    assert work_catalogue.fingerprint(before) != work_catalogue.fingerprint(after)


_CONTRACT_1_PATHS = sorted([
    "work/index.md", "work/now.md", "work/roadmap.md", "work/blocked.md", "work/soaking.md",
    "work/parked.md", "work/recently-completed.md", "work/initiatives/example-upgrade.md",
    "work/initiatives/closed-meanwhile.md",
])


def _run_publication(monkeypatch, state, artifact):
    calls = []

    class RecordingClient:
        def __init__(self, *args):
            pass

        def call(self, method, path, body=None, key=None):
            calls.append((method, path, body, key))
            if method == "GET" and path.startswith("/api/v1/pages?path="):
                requested = path.removeprefix("/api/v1/pages?path=").removesuffix("&status=all")
                return {"pages": [{
                    "resource_id": f"resource-{requested}", "path": requested,
                    "revision": "revision-1", "status": "active",
                }]}
            if method == "POST" and path == "/api/v1/changes":
                return {"change_id": "change-1"}
            if method == "POST" and path.endswith("/publish"):
                return {"publication_receipt": {"deployment": {"status": "COMPLETED"}}}
            return {}

    import schema_catalogue as sc

    monkeypatch.setattr(work_catalogue, "Client", RecordingClient)
    monkeypatch.setattr(work_catalogue, "fetch_state", lambda client: state)
    monkeypatch.setattr(work_catalogue, "ensure_source_entity", lambda *_: "source-entity")
    monkeypatch.setattr(sc, "current_artifact", lambda *a: artifact)
    monkeypatch.setattr(sc, "last_generation_fingerprint", lambda *a: "earlier-fingerprint")
    monkeypatch.setattr(sc, "reconcile_catalogues", lambda *_args, **_kwargs: [])
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN", "not-printed")
    assert work_catalogue.main([]) == 0
    change = next(body for method, path, body, _key in calls if method == "POST" and path == "/api/v1/changes")
    operations = [body for method, path, body, _key in calls if method == "POST" and path.endswith("/operations")]
    archived = sorted(op["page_resource_id"].removeprefix("resource-")
                      for op in operations if op["operation_type"] == "ARCHIVE_PAGE")
    replaced = sorted(op["payload"]["path"] for op in operations if op["operation_type"] == "REPLACE_DOCUMENT")
    return change["generated_ownership_plan"], archived, replaced


def test_phase_a_succeeds_contract_1_and_keeps_every_legacy_path_live(monkeypatch, capsys):
    """Phase A: the contract-2 declaration takes over the contract-1 page set
    and keeps rendering it, so no existing path breaks while callers move.
    Only a page contract 1 would itself have archived (a closed initiative)
    is archived."""
    assert work_catalogue.LEGACY_COEXISTENCE is True
    state = _state([_initiative(work_state="ACTIVE")])
    plan, archived, replaced = _run_publication(monkeypatch, state, {
        "artifact_id": "artifact-v1", "generator_version": "1.0.1", "projection_contract_version": 1,
        "source_entity_id": "source-entity", "redaction_policy": "canonical",
        "target_page_paths": _CONTRACT_1_PATHS, "version": 4,
    })
    live = [path for path in _CONTRACT_1_PATHS if path != "work/initiatives/closed-meanwhile.md"]
    assert plan["mode"] == "SUCCESSOR" and plan["predecessor_id"] == "artifact-v1"
    assert plan["successor"]["projection_contract_version"] == 2
    assert plan["target_page_paths"] == live
    assert archived == ["work/initiatives/closed-meanwhile.md"]
    assert replaced == live
    assert "archived 1 stale path(s)" in capsys.readouterr().out


def test_legacy_pages_during_coexistence_are_the_contract_1_rendering():
    initiative = _initiative(work_state="ACTIVE")
    activities = [{"activity_type": "NOTE", "body": f"full body {n}", "created_at": "2026-09-01T00:00:00Z"}
                  for n in range(5)]
    state = _state([initiative], details={initiative["initiative_id"]: {"activities": activities, "links": []}})
    pages = {p["path"]: p["content"] for p in work_catalogue.render_pages(state, work_catalogue.fingerprint(state))}
    legacy = pages["work/initiatives/example-upgrade.md"]
    # the full history, as contract 1 rendered it — nothing a reader relied on disappears early
    assert all(f"full body {n}" in legacy for n in range(5))
    assert "(initiatives/example-upgrade.md)" in pages["work/now.md"]
    # while the catalogue carries only the bounded preview
    assert "full body 0" not in pages["work/index.md"] and "full body 4" in pages["work/index.md"]


def test_phase_c_retires_every_legacy_page_in_one_change(monkeypatch, capsys):
    """Phase C (coexistence removed): the same contract-2 declaration shrinks
    to work/index.md in place and archives every legacy page it owned."""
    monkeypatch.setattr(work_catalogue, "LEGACY_COEXISTENCE", False)
    state = _state([_initiative(work_state="ACTIVE")])
    coexisting = [path for path in _CONTRACT_1_PATHS if path != "work/initiatives/closed-meanwhile.md"]
    plan, archived, replaced = _run_publication(monkeypatch, state, {
        "artifact_id": "artifact-v2", "generator_version": work_catalogue.GENERATOR_VERSION,
        "projection_contract_version": work_catalogue.PROJECTION_CONTRACT_VERSION,
        "source_entity_id": "source-entity", "redaction_policy": "canonical",
        "target_page_paths": coexisting, "version": 2,
    })
    assert plan["mode"] == "IN_PLACE" and plan["target_page_paths"] == ["work/index.md"]
    assert archived == [path for path in coexisting if path != "work/index.md"]
    assert replaced == ["work/index.md"]
    assert f"archived {len(coexisting) - 1} stale path(s)" in capsys.readouterr().out


def test_rendering_is_deterministic():
    state = _state([_initiative()])
    fp = work_catalogue.fingerprint(state)
    assert work_catalogue.render_pages(state, fp) == work_catalogue.render_pages(state, fp)


_ACCESS_KEY_SHAPED = "AKIA" + "A" * 16
_TOKEN_SHAPED = "ghp_" + "b" * 36
_BEARER_SHAPED = "Bearer " + "c" * 24
_HEX_SECRET_SHAPED = "d" * 40


def test_render_pages_apply_canonical_redaction_to_all_free_text_fields():
    blocked = _initiative(
        initiative_key="blocked-secret",
        work_state="BLOCKED",
        objective=f"objective {_ACCESS_KEY_SHAPED}",
        blocker_summary=f"blocked by {_BEARER_SHAPED}",
    )
    soaking = _initiative(
        initiative_id="00000000-0000-0000-0000-000000000002",
        initiative_key="soak-secret",
        title="Soak secret",
        work_state="SOAKING",
        objective="ordinary objective",
        soak_success_criteria=f"success {_HEX_SECRET_SHAPED}",
        soak_failure_conditions=f"failure {_ACCESS_KEY_SHAPED}",
        soak_monitoring_ref=f"monitor {_TOKEN_SHAPED}",
    )
    details = {
        blocked["initiative_id"]: {
            "activities": [{
                "activity_type": "NOTE",
                "body": f"activity {_TOKEN_SHAPED}",
                "created_at": "2026-08-26T00:00:00Z",
            }],
            "links": [{
                "relation": "EVIDENCE",
                "resource_type": "EXTERNAL",
                "resource_id": f"reference-{_ACCESS_KEY_SHAPED}",
            }],
        },
    }
    state = _state([blocked, soaking], details=details)
    fp = work_catalogue.fingerprint(state)
    content = "\n".join(page["content"] for page in work_catalogue.render_pages(state, fp))

    for secret_shape in (_ACCESS_KEY_SHAPED, _TOKEN_SHAPED, _BEARER_SHAPED, _HEX_SECRET_SHAPED):
        assert secret_shape not in content
    assert "<REDACTED:ACCESS_KEY:work-catalogue>" in content
    assert "<REDACTED:TOKEN:work-catalogue>" in content
    assert "<REDACTED:BEARER:work-catalogue>" in content
    assert "<REDACTED:HEX_SECRET:work-catalogue>" in content


def test_clean_rendering_is_byte_equivalent_to_the_unredacted_renderer():
    state = _state([_initiative()])
    fp = work_catalogue.fingerprint(state)
    assert work_catalogue.render_pages(state, fp) == work_catalogue._render_pages_unredacted(state, fp)


def test_render_redaction_is_idempotent_and_does_not_change_source_fingerprint():
    initiative = _initiative(objective=f"remove {_TOKEN_SHAPED}")
    state = _state([initiative])
    before = work_catalogue.fingerprint(state)
    pages = work_catalogue.render_pages(state, before)
    after = work_catalogue.fingerprint(state)

    assert before == after
    for page in pages:
        assert redact(page["content"], label="work-catalogue").sanitised == page["content"]


def test_malformed_marker_is_rejected_at_render_boundary():
    state = _state([_initiative(objective="broken {<REDACTED:TOKEN:legacy>}} marker")])
    with pytest.raises(MalformedMarkerError):
        work_catalogue.render_pages(state, work_catalogue.fingerprint(state))


def test_redaction_refusal_prevents_every_publication_and_preserves_last_known_good(monkeypatch):
    unsafe = f"```text\nprefix-{_TOKEN_SHAPED}-suffix\n```"
    state = _state([_initiative(objective=unsafe)])
    calls = []

    class RecordingClient:
        def __init__(self, *args):
            pass

        def call(self, method, path, body=None, key=None):
            calls.append((method, path, body, key))
            raise AssertionError("redaction refusal must occur before any API mutation")

    monkeypatch.setattr(work_catalogue, "Client", RecordingClient)
    monkeypatch.setattr(work_catalogue, "fetch_state", lambda client: state)
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN", "not-printed")

    with pytest.raises(DocumentRefusedError) as refused:
        work_catalogue.main([])
    assert calls == []
    assert refused.value.findings
    assert all(_TOKEN_SHAPED not in str(finding) for finding in refused.value.findings)


def test_source_probe_records_only_entity_scoped_canonical_freshness(monkeypatch, capsys):
    state = _state([_initiative()])
    expected = work_catalogue.fingerprint(state)
    calls = []

    class RecordingClient:
        def __init__(self, *args):
            pass

        def call(self, method, path, body=None, key=None):
            calls.append((method, path, body, key))
            if method == "GET" and path.startswith("/api/v1/model/entities?"):
                return {"entities": [{
                    "entity_id": "00000000-0000-0000-0000-000000000099",
                    "entity_kind": "SYSTEM",
                    "entity_key": "docplane-work",
                }]}
            if method == "POST" and path == "/api/v1/observations":
                return {"recorded": [{"observation_id": "observation-1", "replayed": False}]}
            raise AssertionError(f"unexpected probe call: {method} {path}")

    probe_id = "00000000-0000-0000-0000-000000000123"
    monkeypatch.setattr(work_catalogue, "Client", RecordingClient)
    monkeypatch.setattr(work_catalogue, "fetch_state", lambda client: state)
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN", "not-printed")

    assert work_catalogue.main(["--observe-source", "--probe-id", probe_id, "--status-json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "observation_id": "observation-1",
        "outcome": "NOMINAL",
        "probe_id": probe_id,
        "source_entity": {
            "entity_id": "00000000-0000-0000-0000-000000000099",
            "entity_key": "docplane-work",
            "entity_kind": "SYSTEM",
        },
        "source_fingerprint": expected,
    }
    writes = [call for call in calls if call[0] != "GET"]
    assert len(writes) == 1
    method, path, body, key = writes[0]
    assert (method, path, key) == (
        "POST", "/api/v1/observations", f"work-catalogue:source-probe:{probe_id}:batch"
    )
    observation = body["observations"][0]
    assert observation == {
        "subject_entity_id": "00000000-0000-0000-0000-000000000099",
        "observation_kind": "FRESHNESS_CHECK",
        "outcome": "NOMINAL",
        "summary": "Observed authoritative WORK state for work-catalogue",
        "payload": {"probe": "work-catalogue-source"},
        "idempotency_key": f"work-catalogue:source-probe:{probe_id}:observation",
        "source_fingerprint": expected,
    }


@pytest.mark.parametrize("entities", [[], [
    {"entity_id": "one", "entity_kind": "SYSTEM", "entity_key": "docplane-work"},
    {"entity_id": "two", "entity_kind": "SYSTEM", "entity_key": "docplane-work"},
]])
def test_source_probe_never_repairs_missing_or_ambiguous_source(monkeypatch, capsys, entities):
    calls = []

    class RecordingClient:
        def __init__(self, *args):
            pass

        def call(self, method, path, body=None, key=None):
            calls.append((method, path, body, key))
            return {"entities": entities}

    monkeypatch.setattr(work_catalogue, "Client", RecordingClient)
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN", "not-printed")

    assert work_catalogue.main(["--observe-source"]) == 1
    assert json.loads(capsys.readouterr().err)["error_class"] == "RuntimeError"
    assert calls and all(call[0] == "GET" for call in calls)
    assert all(call[1] != "/api/v1/model/entities" for call in calls)


@pytest.mark.parametrize(
    ("stage", "failure"),
    [
        ("FETCH_WORK_STATE", "fetch_state"),
        ("CANONICALIZE_SOURCE", "fingerprint"),
    ],
)
def test_source_probe_records_bounded_failed_evidence(monkeypatch, capsys, stage, failure):
    state = _state([_initiative()])
    calls = []

    class ProbeFailure(Exception):
        pass

    class RecordingClient:
        def __init__(self, *args):
            pass

        def call(self, method, path, body=None, key=None):
            calls.append((method, path, body, key))
            if method == "GET":
                return {"entities": [{
                    "entity_id": "source-entity", "entity_kind": "SYSTEM", "entity_key": "docplane-work",
                }]}
            return {"recorded": [{"observation_id": "failed-observation", "replayed": False}]}

    monkeypatch.setattr(work_catalogue, "Client", RecordingClient)
    monkeypatch.setattr(work_catalogue, "fetch_state", lambda client: state)
    if failure == "fetch_state":
        monkeypatch.setattr(work_catalogue, "fetch_state", lambda client: (_ for _ in ()).throw(ProbeFailure()))
    else:
        monkeypatch.setattr(work_catalogue, "fingerprint", lambda value: (_ for _ in ()).throw(ProbeFailure()))
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN", "not-printed")

    assert work_catalogue.main([
        "--observe-source", "--probe-id", "00000000-0000-0000-0000-000000000124"
    ]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "FAILED"
    observation = next(call[2]["observations"][0] for call in calls if call[0] == "POST")
    assert "source_fingerprint" not in observation
    assert observation["payload"] == {
        "probe": "work-catalogue-source", "stage": stage, "error_class": "ProbeFailure",
    }
    assert "ProbeFailure" not in observation["summary"]


def test_source_probe_does_not_claim_failed_evidence_when_observation_write_fails(monkeypatch, capsys):
    class SourceReadFailure(Exception):
        pass

    class ObservationWriteFailure(Exception):
        pass

    class FailingClient:
        def __init__(self, *args):
            pass

        def call(self, method, path, body=None, key=None):
            if method == "GET":
                return {"entities": [{
                    "entity_id": "source-entity", "entity_kind": "SYSTEM", "entity_key": "docplane-work",
                }]}
            raise ObservationWriteFailure()

    monkeypatch.setattr(work_catalogue, "Client", FailingClient)
    monkeypatch.setattr(
        work_catalogue, "fetch_state",
        lambda client: (_ for _ in ()).throw(SourceReadFailure()),
    )
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN", "not-printed")

    assert work_catalogue.main(["--observe-source"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err)["error_class"] == "ObservationWriteFailure"


def test_probe_and_generation_emit_the_same_fingerprint_for_identical_work(monkeypatch):
    state = _state([_initiative()])
    expected = work_catalogue.fingerprint(state)
    paths = work_catalogue.desired_page_paths(state)
    observations = []

    class RecordingClient:
        def __init__(self, *args):
            pass

        def call(self, method, path, body=None, key=None):
            if method == "GET" and path.startswith("/api/v1/model/entities?"):
                return {"entities": [{
                    "entity_id": "source-entity", "entity_kind": "SYSTEM", "entity_key": "docplane-work",
                }]}
            if method == "POST" and path == "/api/v1/observations":
                observations.append(body["observations"][0])
                return {"recorded": [{"observation_id": f"observation-{len(observations)}", "replayed": False}]}
            raise AssertionError(f"unexpected call: {method} {path}")

    import schema_catalogue as sc

    monkeypatch.setattr(work_catalogue, "Client", RecordingClient)
    monkeypatch.setattr(work_catalogue, "fetch_state", lambda client: state)
    monkeypatch.setattr(sc, "current_artifact", lambda *args: {
        "artifact_id": "artifact", "generator_version": work_catalogue.GENERATOR_VERSION,
        "projection_contract_version": work_catalogue.PROJECTION_CONTRACT_VERSION,
        "target_page_paths": paths, "version": 1,
    })
    monkeypatch.setattr(sc, "last_generation_fingerprint", lambda *args: expected)
    monkeypatch.setattr(
        sc, "page_ids_for_paths", lambda _client, paths: {path: f"id-{path}" for path in paths}
    )
    monkeypatch.setattr(sc, "reconcile_catalogues", lambda *_args, **_kwargs: [])
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN", "not-printed")

    assert work_catalogue.main([
        "--observe-source", "--probe-id", "00000000-0000-0000-0000-000000000125"
    ]) == 0
    assert work_catalogue.main([]) == 0
    probe, generation = observations
    assert probe["observation_kind"] == "FRESHNESS_CHECK"
    assert generation["observation_kind"] == "GENERATION"
    assert probe["source_fingerprint"] == generation["source_fingerprint"] == expected


def test_unchanged_main_replays_observation_and_never_publishes(monkeypatch, capsys):
    state = _state([_initiative()])
    fp = work_catalogue.fingerprint(state)
    paths = sorted(p["path"] for p in work_catalogue.render_pages(state, fp))
    calls = []

    class RecordingClient:
        def __init__(self, *args):
            pass

        def call(self, method, path, body=None, key=None):
            calls.append((method, path))
            return {}

    import schema_catalogue as sc

    monkeypatch.setattr(work_catalogue, "Client", RecordingClient)
    monkeypatch.setattr(work_catalogue, "fetch_state", lambda client: state)
    monkeypatch.setattr(work_catalogue, "ensure_source_entity", lambda *_: "source-entity")
    monkeypatch.setattr(sc, "current_artifact", lambda *a: {
        "artifact_id": "artifact", "generator_version": work_catalogue.GENERATOR_VERSION,
        "projection_contract_version": work_catalogue.PROJECTION_CONTRACT_VERSION,
        "target_page_paths": paths, "version": 1,
    })
    monkeypatch.setattr(sc, "last_generation_fingerprint", lambda *a: fp)
    monkeypatch.setattr(
        sc, "page_ids_for_paths", lambda _client, wanted: {path: f"id-{path}" for path in wanted}
    )
    monkeypatch.setattr(sc, "reconcile_catalogues", lambda *_args, **_kwargs: [])
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN", "not-printed")

    assert work_catalogue.main([]) == 0
    assert "UNCHANGED" in capsys.readouterr().out
    assert [path for _, path in calls] == ["/api/v1/observations"]


def test_membership_and_software_version_do_not_require_succession():
    artifact = {
        "projection_contract_version": work_catalogue.PROJECTION_CONTRACT_VERSION,
        "generator_version": "older-build",
        "target_page_paths": ["work/index.md"],
    }
    assert work_catalogue.needs_succession(artifact) is False
    assert work_catalogue.needs_reconciliation(artifact, ["work/index.md", "work/now.md"]) is True
    assert work_catalogue.needs_reconciliation(artifact, ["work/index.md"]) is True
    assert work_catalogue.needs_succession(
        {**artifact, "projection_contract_version": work_catalogue.PROJECTION_CONTRACT_VERSION + 1}
    ) is True
    # The deployed page-per-initiative declaration is contract 1.
    assert work_catalogue.needs_succession({**artifact, "projection_contract_version": 1}) is True


def test_work_catalogues_mapping_is_exactly_the_system_index():
    assert work_catalogue.work_catalogues_mappings(
        "system-docplane-work",
        {"work/index.md": "page-index", "work/now.md": "page-now"},
    ) == {"system-docplane-work": ["page-index"]}


def test_unchanged_work_repairs_semantics_without_publication_before_generation(monkeypatch):
    state = _state([_initiative()])
    fp = work_catalogue.fingerprint(state)
    paths = work_catalogue.desired_page_paths(state)
    events = []

    class Client:
        def __init__(self, *args): pass
        def call(self, method, path, body=None, key=None):
            if path == "/api/v1/observations":
                events.append("generation")
                return {"recorded": [{"replayed": True}]}
            raise AssertionError(f"unexpected API call: {method} {path}")

    import schema_catalogue as sc

    monkeypatch.setattr(work_catalogue, "Client", Client)
    monkeypatch.setattr(work_catalogue, "fetch_state", lambda _client: state)
    monkeypatch.setattr(work_catalogue, "ensure_source_entity", lambda *_: "system-1")
    monkeypatch.setattr(sc, "current_artifact", lambda *_: {
        "artifact_id": "artifact-1", "generator_version": work_catalogue.GENERATOR_VERSION,
        "projection_contract_version": work_catalogue.PROJECTION_CONTRACT_VERSION,
        "target_page_paths": paths, "version": 1,
    })
    monkeypatch.setattr(sc, "last_generation_fingerprint", lambda *_: fp)
    monkeypatch.setattr(sc, "page_ids_for_paths", lambda _client, wanted: {
        path: f"id-{path}" for path in wanted
    })
    monkeypatch.setattr(
        sc, "reconcile_catalogues",
        lambda *_args, **_kwargs: events.append("catalogues") or [{"changed": True}],
    )
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN", "not-printed")

    assert work_catalogue.main([]) == 0
    assert events == ["catalogues", "generation"]


def test_archived_catalogue_page_is_restored_then_replaced(monkeypatch, capsys):
    """A generated path remains unique while archived.  Regeneration must
    restore that resource, not try to create a duplicate page, and the
    restore/replace operations need distinct idempotency identities."""
    state = _state([_initiative(work_state="ACTIVE")])
    fp = work_catalogue.fingerprint(state)
    pages = work_catalogue.render_pages(state, fp)
    desired_paths = sorted(page["path"] for page in pages)
    reopened_path = "work/index.md"
    calls = []

    class RecordingClient:
        published = False

        def __init__(self, *args):
            pass

        def call(self, method, path, body=None, key=None):
            calls.append((method, path, body, key))
            if method == "GET" and path.startswith("/api/v1/pages?path="):
                requested = path.removeprefix("/api/v1/pages?path=").removesuffix("&status=all")
                page = next(candidate for candidate in pages if candidate["path"] == requested)
                return {"pages": [{
                    "resource_id": f"resource-{desired_paths.index(requested)}",
                    "path": requested,
                    "revision": "revision-1",
                    "status": "active" if self.published or requested != reopened_path else "archived",
                    "title": page["title"],
                }]}
            if method == "POST" and path == "/api/v1/changes":
                return {"change_id": "change-1"}
            if method == "POST" and path.endswith("/publish"):
                self.published = True
                return {"publication_receipt": {"deployment": {"status": "COMPLETED"}}}
            return {}

    import schema_catalogue as sc

    monkeypatch.setattr(work_catalogue, "Client", RecordingClient)
    monkeypatch.setattr(work_catalogue, "fetch_state", lambda client: state)
    monkeypatch.setattr(work_catalogue, "ensure_source_entity", lambda *_: "source-entity")
    monkeypatch.setattr(sc, "current_artifact", lambda *a: {
        "artifact_id": "artifact", "generator_version": work_catalogue.GENERATOR_VERSION,
        "projection_contract_version": work_catalogue.PROJECTION_CONTRACT_VERSION,
        "target_page_paths": desired_paths, "version": 1,
    })
    monkeypatch.setattr(sc, "last_generation_fingerprint", lambda *a: "stale")
    monkeypatch.setattr(sc, "reconcile_catalogues", lambda *_args, **_kwargs: [])
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN", "not-printed")

    assert work_catalogue.main([]) == 0
    assert "PUBLISHED" in capsys.readouterr().out
    operations = [call for call in calls if call[0] == "POST" and call[1].endswith("/operations")]
    reopened_resource = f"resource-{desired_paths.index(reopened_path)}"
    reopened = [call for call in operations if call[2].get("page_resource_id") == reopened_resource]
    assert [call[2]["operation_type"] for call in reopened] == ["RESTORE_PAGE", "REPLACE_DOCUMENT"]
    assert reopened[0][2]["payload"] == {}
    assert reopened[0][3] != reopened[1][3]
    assert all(f":{work_catalogue.GENERATOR_VERSION}:" in call[3] for call in reopened)


def test_dry_run_performs_no_writes(monkeypatch, capsys):
    state = _state([_initiative()])

    class RefusingClient:
        def __init__(self, *args):
            pass

        def call(self, method, path, body=None, key=None):
            raise AssertionError(f"dry run must not call the API: {method} {path}")

    monkeypatch.setattr(work_catalogue, "Client", RefusingClient)
    monkeypatch.setattr(work_catalogue, "fetch_state", lambda client: state)
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN", "not-printed")
    assert work_catalogue.main(["--dry-run"]) == 0
    assert "DRY-RUN" in capsys.readouterr().out


def test_missing_dedicated_token_fails_fast_without_fallback(monkeypatch):
    """Unchanged intent: with no dedicated token configured, fail fast rather than borrow
    another principal's credential. SECRETS-V3 moved the resolution into secret_source, so
    the message now names BOTH accepted sources -- the file path and the legacy variable."""
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_TOKEN", "wrong-principal")
    monkeypatch.delenv("DOCPLANE_WORK_CATALOGUE_TOKEN", raising=False)
    monkeypatch.delenv("DOCPLANE_WORK_CATALOGUE_TOKEN_FILE", raising=False)
    try:
        work_catalogue.main(["--dry-run"])
        raise AssertionError("missing dedicated automation token must fail")
    except RuntimeError as exc:
        message = str(exc)
        assert "DOCPLANE_WORK_CATALOGUE_TOKEN_FILE" in message
        assert "DOCPLANE_WORK_CATALOGUE_TOKEN" in message
        assert "refusing to fall back to another principal" in message
        # The other principal's credential must never appear, used or quoted.
        assert "wrong-principal" not in message


def test_dedicated_token_file_is_used_and_env_is_not(monkeypatch, tmp_path):
    """SECRETS-V3: when the runtime secret file is configured it wins, and a stale legacy
    environment value for the same principal is ignored entirely."""
    token_file = tmp_path / "token"
    token_file.write_text("FROM-RUNTIME-FILE")
    token_file.chmod(0o600)
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN", "STALE-ENV-VALUE")

    seen = {}

    class RecordingClient:
        def __init__(self, api, token):
            seen["api"] = api
            seen["token"] = token

    monkeypatch.setattr(work_catalogue, "Client", RecordingClient)
    try:
        work_catalogue.main(["--dry-run"])
    except Exception:
        pass                      # the dry run may fail later; the credential choice is the point
    assert seen.get("token") == "FROM-RUNTIME-FILE"


def test_unreadable_token_file_does_not_revive_the_legacy_value(monkeypatch, tmp_path):
    """The rule carrying the security weight: once _FILE is configured, a materialization
    failure must be fatal, not a silent fallback to plaintext environment delivery."""
    import os

    if os.geteuid() == 0:
        import pytest as _pytest

        _pytest.skip("running as root: DAC does not deny root")
    token_file = tmp_path / "token"
    token_file.write_text("FROM-RUNTIME-FILE")
    token_file.chmod(0o000)
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN", "STALE-ENV-VALUE")
    try:
        work_catalogue.main(["--dry-run"])
        raise AssertionError("an unreadable token file must fail, not fall back")
    except RuntimeError as exc:
        assert "STALE-ENV-VALUE" not in str(exc)


def test_status_reports_drift_and_writes_atomic_metrics(monkeypatch, tmp_path, capsys):
    state = _state([_initiative()])

    class RecordingClient:
        def __init__(self, *args):
            pass

    import schema_catalogue as sc

    monkeypatch.setattr(work_catalogue, "Client", RecordingClient)
    monkeypatch.setattr(work_catalogue, "fetch_state", lambda client: state)
    monkeypatch.setattr(sc, "current_artifact", lambda *a: {"artifact_id": "artifact"})
    monkeypatch.setattr(sc, "last_generation_fingerprint", lambda *a: "stale")
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN", "not-printed")
    metrics = tmp_path / "work.prom"

    assert work_catalogue.main(["--status-json", "--metrics-file", str(metrics), "--reconcile-success", "0"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["drift"] is True
    assert status["reconcile_success"] is False
    text = metrics.read_text(encoding="utf-8")
    assert 'docplane_generated_projection_drift{artifact="work-catalogue"} 1' in text
    assert 'docplane_generated_projection_reconcile_success{artifact="work-catalogue"} 0' in text


def test_status_reports_generator_contract_drift_at_same_fingerprint(monkeypatch, capsys):
    state = _state([_initiative()])
    fp = work_catalogue.fingerprint(state)

    class RecordingClient:
        def __init__(self, *args):
            pass

    import schema_catalogue as sc

    monkeypatch.setattr(work_catalogue, "Client", RecordingClient)
    monkeypatch.setattr(work_catalogue, "fetch_state", lambda client: state)
    monkeypatch.setattr(sc, "current_artifact", lambda *a: {
        "artifact_id": "artifact",
        "generator_version": "1.0.0",
        "target_page_paths": work_catalogue.desired_page_paths(state),
    })
    monkeypatch.setattr(sc, "last_generation_fingerprint", lambda *a: fp)
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN", "not-printed")

    assert work_catalogue.main(["--status-json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["live_fingerprint"] == status["published_fingerprint"] == fp
    assert status["drift"] is True


def test_status_probe_does_not_render_pages(monkeypatch, tmp_path):
    """The 15-minute probe needs the fingerprint only; rendering every page
    would double each tick's cost for output it discards."""
    state = _state([_initiative()])
    rendered = []

    class RecordingClient:
        def __init__(self, *args):
            pass

    import schema_catalogue as sc

    monkeypatch.setattr(work_catalogue, "Client", RecordingClient)
    monkeypatch.setattr(work_catalogue, "fetch_state", lambda client: state)
    monkeypatch.setattr(sc, "current_artifact", lambda *a: {"artifact_id": "artifact"})
    monkeypatch.setattr(sc, "last_generation_fingerprint", lambda *a: "stale")
    original_render = work_catalogue.render_pages
    monkeypatch.setattr(
        work_catalogue, "render_pages",
        lambda *args, **kwargs: rendered.append(1) or original_render(*args, **kwargs),
    )
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN", "not-printed")

    assert work_catalogue.main(["--metrics-file", str(tmp_path / "work.prom")]) == 0
    assert rendered == []


def test_scheduler_wrapper_treats_a_lock_conflict_as_skipped_not_failed():
    """flock's conflict exit must not be reported as a failed reconciliation:
    the lock doing its job would otherwise raise the failure alert and fail
    the systemd unit. The lock also lives outside /tmp, which PrivateTmp=true
    would otherwise make private to the timer."""
    script = (ROOT / "scripts" / "run_work_catalogue_reconciliation.sh").read_text(encoding="utf-8")
    assert "FLOCK_CONFLICT_EXIT=75" in script
    assert 'flock -n -E "$FLOCK_CONFLICT_EXIT"' in script
    assert "reconcile_status=0" in script
    assert "/run/lock/docplane-work-catalogue.lock" in script
    assert "/tmp/docplane-work-catalogue.lock" not in script


def test_attended_source_probe_uses_the_existing_reconciliation_lock():
    script = (ROOT / "scripts" / "work_catalogue.py").read_text(encoding="utf-8")
    assert "flock -n -E 75 /run/lock/docplane-work-catalogue.lock" in script
    assert "work_catalogue.py --observe-source --status-json" in script


def test_scheduler_units_bound_work_drift_and_use_the_single_writer():
    service = (ROOT / "config" / "systemd" / "docplane-work-catalogue.service").read_text(encoding="utf-8")
    timer = (ROOT / "config" / "systemd" / "docplane-work-catalogue.timer").read_text(encoding="utf-8")
    assert "scripts/run_work_catalogue_reconciliation.sh" in service
    assert "scripts/work_catalogue.py" not in service
    assert "EnvironmentFile=/etc/docplane/work-catalogue.env" in service
    assert "OnUnitInactiveSec=1min" in timer
    assert "Persistent=true" in timer


def _observer_test_environment(tmp_path, exit_code=0):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$OBSERVER_ARGS_FILE\"\n"
        "exit \"$OBSERVER_EXIT_CODE\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": f"{fake_bin}:{env['PATH']}",
        "DOCPLANE_WORK_CATALOGUE_LOCK_FILE": str(tmp_path / "work-catalogue.lock"),
        "OBSERVER_ARGS_FILE": str(tmp_path / "observer-args"),
        "OBSERVER_EXIT_CODE": str(exit_code),
    })
    return env


def test_source_observer_invokes_only_the_canonical_probe_mode(tmp_path):
    wrapper = ROOT / "scripts" / "run_work_catalogue_source_observer.sh"
    env = _observer_test_environment(tmp_path)

    result = subprocess.run(["bash", str(wrapper)], env=env, text=True, capture_output=True)

    assert result.returncode == 0
    args = (tmp_path / "observer-args").read_text(encoding="utf-8").splitlines()
    assert args == [str(ROOT / "scripts" / "work_catalogue.py"), "--observe-source", "--status-json"]
    assert "--dry-run" not in args


def test_source_observer_lock_contention_is_a_benign_skip_without_probe(tmp_path):
    wrapper = ROOT / "scripts" / "run_work_catalogue_source_observer.sh"
    env = _observer_test_environment(tmp_path)
    lock_path = Path(env["DOCPLANE_WORK_CATALOGUE_LOCK_FILE"])

    with lock_path.open("w", encoding="utf-8") as held_lock:
        fcntl.flock(held_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(["bash", str(wrapper)], env=env, text=True, capture_output=True)

    assert result.returncode == 0
    assert "SKIPPED work-catalogue exclusion domain is already held" in result.stderr
    assert not (tmp_path / "observer-args").exists()


def test_source_observer_preserves_genuine_probe_failure(tmp_path):
    wrapper = ROOT / "scripts" / "run_work_catalogue_source_observer.sh"
    env = _observer_test_environment(tmp_path, exit_code=23)

    result = subprocess.run(["bash", str(wrapper)], env=env, text=True, capture_output=True)

    assert result.returncode == 23


def test_source_observer_units_match_the_declared_local_contract():
    service = (ROOT / "config" / "systemd" / "docplane-work-catalogue-observer.service").read_text(
        encoding="utf-8"
    )
    timer = (ROOT / "config" / "systemd" / "docplane-work-catalogue-observer.timer").read_text(
        encoding="utf-8"
    )
    wrapper = (ROOT / "scripts" / "run_work_catalogue_source_observer.sh").read_text(encoding="utf-8")

    assert "EnvironmentFile=/etc/docplane/work-catalogue.env" in service
    assert "scripts/run_work_catalogue_source_observer.sh" in service
    assert "ReadWritePaths=/run/lock" in service
    assert "--observe-source --status-json" in wrapper
    assert "/run/lock/docplane-work-catalogue.lock" in wrapper
    assert "FLOCK_CONFLICT_EXIT=75" in wrapper
    assert "OnActiveSec=2min" in timer
    assert "OnUnitInactiveSec=10min" in timer
    assert "AccuracySec=30s" in timer
    assert "RandomizedDelaySec=30s" in timer
    assert "Persistent=false" in timer
    assert 10 * 60 + 30 + 30 < 1800

    combined = service + timer + wrapper
    assert "github" not in combined.lower()
    assert "/api/v1/initiatives" not in combined
    assert "Authorization" not in combined
    assert "DOCPLANE_WORK_CATALOGUE_TOKEN=" not in combined


def test_observer_installation_is_explicitly_non_starting():
    guide = (ROOT / "docs" / "operations" / "WORK_CATALOGUE.md").read_text(encoding="utf-8")
    observer_section = guide.split("## Independent source observation", 1)[1]
    installation = observer_section.split("### Later activation gate", 1)[0]

    assert "docplane-work-catalogue-observer.service" in installation
    assert "docplane-work-catalogue-observer.timer" in installation
    assert "systemctl daemon-reload" in installation
    assert "systemctl enable" not in installation
    assert "systemctl start" not in installation


def test_reader_download_filename_is_prefixed_with_page_version():
    template = (ROOT / "mkdocs" / "overrides" / "main.html").read_text(encoding="utf-8")
    assert "strip.dataset.pageVersion" in template
    assert "'v' + pageVersion + '_'" in template
    assert "+ basename + '.md'" in template
