"""The work catalogue: a browsable, read-only work/ section generated from
initiative state. Tests cover the pure core — projection determinism,
fingerprint sensitivity, the inbox-count-only rule, queue and initiative
rendering — plus the UNCHANGED main path never publishing."""
from __future__ import annotations

import sys
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import work_catalogue  # noqa: E402


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


def test_queue_pages_render_all_states_and_roadmap_ranks_by_priority():
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
    ]
    state = _state(rows)
    fp = work_catalogue.fingerprint(state)
    pages = {p["path"]: p for p in work_catalogue.render_pages(state, fp)}
    for path in ("work/index.md", "work/now.md", "work/roadmap.md", "work/blocked.md",
                 "work/soaking.md", "work/parked.md", "work/recently-completed.md"):
        assert path in pages
    roadmap = pages["work/roadmap.md"]["content"]
    assert roadmap.index("crit") < roadmap.index("low")
    assert "waiting on vendor firmware" in pages["work/blocked.md"]["content"]
    soaking = pages["work/soaking.md"]["content"]
    assert "No alert fires for 14 days" in soaking and "alert:ExampleServiceDown" in soaking
    # Per-initiative pages exist for open states, under stable key-based paths.
    assert "work/initiatives/crit.md" in pages and "work/initiatives/soaker.md" in pages
    # Every page routes action back to the dashboard.
    assert all("/dashboard/#work" in p["content"] for p in pages.values())


def test_completed_page_shows_closure_gate_and_closed_initiatives_get_no_page():
    done = _initiative(initiative_key="shipped", title="Shipped", work_state="COMPLETE",
                       completed_at="2026-07-30T12:00:00Z")
    state = _state([done])
    fp = work_catalogue.fingerprint(state)
    pages = {p["path"]: p for p in work_catalogue.render_pages(state, fp)}
    completed = pages["work/recently-completed.md"]["content"]
    assert "UPDATED / NOT_REQUIRED / DEFERRED" in completed
    assert "work/initiatives/shipped.md" not in pages


def test_abandoned_transition_disappears_from_now_and_archives_its_page():
    active = _initiative(initiative_key="retired-plan", title="Retired plan", work_state="ACTIVE")
    before = _state([active])
    before_pages = {page["path"]: page for page in work_catalogue.render_pages(before, work_catalogue.fingerprint(before))}
    assert "retired-plan" in before_pages["work/now.md"]["content"]
    assert "work/initiatives/retired-plan.md" in before_pages

    abandoned = _initiative(initiative_key="retired-plan", title="Retired plan", work_state="ABANDONED")
    after = _state([abandoned])
    after_pages = {page["path"]: page for page in work_catalogue.render_pages(after, work_catalogue.fingerprint(after))}
    assert "retired-plan" not in after_pages["work/now.md"]["content"]
    assert "work/initiatives/retired-plan.md" not in after_pages
    assert work_catalogue.fingerprint(before) != work_catalogue.fingerprint(after)


def test_rendering_is_deterministic():
    state = _state([_initiative()])
    fp = work_catalogue.fingerprint(state)
    assert work_catalogue.render_pages(state, fp) == work_catalogue.render_pages(state, fp)


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
        "target_page_paths": paths, "version": 1,
    })
    monkeypatch.setattr(sc, "last_generation_fingerprint", lambda *args: expected)
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
    monkeypatch.setattr(sc, "current_artifact", lambda *a: {
        "artifact_id": "artifact", "generator_version": work_catalogue.GENERATOR_VERSION,
        "target_page_paths": paths, "version": 1,
    })
    monkeypatch.setattr(sc, "last_generation_fingerprint", lambda *a: fp)
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_WORK_CATALOGUE_TOKEN", "not-printed")

    assert work_catalogue.main([]) == 0
    assert "UNCHANGED" in capsys.readouterr().out
    assert [path for _, path in calls] == ["/api/v1/observations"]


def test_reopened_initiative_restores_then_replaces_archived_page(monkeypatch, capsys):
    """A closed initiative's path remains unique while archived.  Reopening
    must restore that resource, not try to create a duplicate page, and the
    restore/replace operations need distinct idempotency identities."""
    state = _state([_initiative(work_state="ACTIVE")])
    fp = work_catalogue.fingerprint(state)
    pages = work_catalogue.render_pages(state, fp)
    desired_paths = sorted(page["path"] for page in pages)
    reopened_path = "work/initiatives/example-upgrade.md"
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
    monkeypatch.setattr(sc, "current_artifact", lambda *a: {
        "artifact_id": "artifact", "generator_version": work_catalogue.GENERATOR_VERSION,
        "target_page_paths": desired_paths, "version": 1,
    })
    monkeypatch.setattr(sc, "last_generation_fingerprint", lambda *a: "stale")
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
    monkeypatch.setenv("DOCPLANE_API", "https://docplane.invalid")
    monkeypatch.setenv("DOCPLANE_TOKEN", "wrong-principal")
    monkeypatch.delenv("DOCPLANE_WORK_CATALOGUE_TOKEN", raising=False)
    try:
        work_catalogue.main(["--dry-run"])
        raise AssertionError("missing dedicated automation token must fail")
    except RuntimeError as exc:
        assert "DOCPLANE_WORK_CATALOGUE_TOKEN is required" in str(exc)


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


def test_reader_download_filename_is_prefixed_with_page_version():
    template = (ROOT / "mkdocs" / "overrides" / "main.html").read_text(encoding="utf-8")
    assert "strip.dataset.pageVersion" in template
    assert "'v' + pageVersion + '_'" in template
    assert "+ basename + '.md'" in template
