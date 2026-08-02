"""The work catalogue: a browsable, read-only work/ section generated from
initiative state. Tests cover the pure core — projection determinism,
fingerprint sensitivity, the inbox-count-only rule, queue and initiative
rendering — plus the UNCHANGED main path never publishing."""
from __future__ import annotations

import sys
from pathlib import Path

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


def test_rendering_is_deterministic():
    state = _state([_initiative()])
    fp = work_catalogue.fingerprint(state)
    assert work_catalogue.render_pages(state, fp) == work_catalogue.render_pages(state, fp)


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
