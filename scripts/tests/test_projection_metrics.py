"""The shared generated-projection metrics writer.

Every generated projection publishes the SAME three series, separated by the `artifact` label,
so one set of alerts covers all of them and a new projection cannot quietly invent a metric
name nobody reads. These tests pin the contract the alerts depend on.

Inert: no DocPlane, no credentials, no network.
"""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))


def _write(tmp_path, **kwargs):
    import schema_catalogue as sc
    target = tmp_path / "sub" / "projection.prom"
    sc.write_projection_metrics(str(target), **kwargs)
    return target


def test_series_names_and_label(tmp_path):
    target = _write(tmp_path, artifact="meter-list-hub2.prometheus", drift=False, success=True)
    text = target.read_text()
    assert 'docplane_generated_projection_drift{artifact="meter-list-hub2.prometheus"} 0' in text
    assert 'docplane_generated_projection_reconcile_success{artifact="meter-list-hub2.prometheus"} 1' in text
    assert 'docplane_generated_projection_last_run_unixtime{artifact="meter-list-hub2.prometheus"}' in text
    # Each series is declared, or the collector rejects the file.
    assert text.count("# HELP") == 3 and text.count("# TYPE") == 3


def test_drift_and_failure_are_reported_as_one(tmp_path):
    text = _write(tmp_path, artifact="work-catalogue", drift=True, success=False).read_text()
    assert 'docplane_generated_projection_drift{artifact="work-catalogue"} 1' in text
    assert 'docplane_generated_projection_reconcile_success{artifact="work-catalogue"} 0' in text


def test_two_artifacts_do_not_collide(tmp_path):
    a = _write(tmp_path / "a", artifact="work-catalogue", drift=False, success=True).read_text()
    b = _write(tmp_path / "b", artifact="meter-list-hub2.prometheus", drift=True, success=True).read_text()
    assert 'artifact="work-catalogue"' in a and 'artifact="meter-list' not in a
    assert 'artifact="meter-list-hub2.prometheus"' in b and 'artifact="work-catalogue"' not in b


def test_file_is_readable_by_the_collector_and_left_complete(tmp_path):
    target = _write(tmp_path, artifact="work-catalogue", drift=False, success=True)
    assert oct(target.stat().st_mode)[-3:] == "644"
    # The rename is atomic, so no partial temp file is left behind to be scraped.
    assert [p.name for p in target.parent.iterdir()] == [target.name]


def test_rewrite_replaces_rather_than_appends(tmp_path):
    target = _write(tmp_path, artifact="work-catalogue", drift=True, success=True)
    import schema_catalogue as sc
    sc.write_projection_metrics(str(target), artifact="work-catalogue", drift=False, success=True)
    text = target.read_text()
    assert text.count("docplane_generated_projection_drift{") == 1
    assert 'docplane_generated_projection_drift{artifact="work-catalogue"} 0' in text
