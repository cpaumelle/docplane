"""Unit tests for the docs lifecycle validator (F7.2).

Pure — imports app/lifecycle.py directly (stdlib-only), so no DB / FastAPI / key needed.
Run:  python3 -m pytest docs-api/tests/test_lifecycle.py -q
   or: python3 docs-api/tests/test_lifecycle.py   (direct, no pytest)
"""
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "lifecycle", os.path.join(os.path.dirname(__file__), "..", "app", "lifecycle.py"))
lifecycle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lifecycle)

_GOOD = "**Lifecycle:** REFERENCE\n<!-- lifecycle: REFERENCE -->\n# Title\nbody"


def test_well_formed_page_is_clean():
    errors, warnings = lifecycle.lifecycle_findings([("a.md", _GOOD)])
    assert errors == [] and warnings == []


def test_missing_marker_is_an_error():
    errors, _ = lifecycle.lifecycle_findings([("a.md", "# Title\nno lifecycle here")])
    assert errors and errors[0]["rule"] == "lifecycle-missing"


def test_field_and_comment_disagree_is_an_error():
    text = "**Lifecycle:** ACTIVE\n<!-- lifecycle: DEPRECATED -->"
    errors, _ = lifecycle.lifecycle_findings([("a.md", text)])
    assert any(e["rule"] == "lifecycle-inconsistent" for e in errors)


def test_unknown_state_is_a_warning_not_error():
    text = "**Lifecycle:** BOGUS\n<!-- lifecycle: BOGUS -->"
    errors, warnings = lifecycle.lifecycle_findings([("a.md", text)])
    assert errors == []
    assert warnings and warnings[0]["rule"] == "lifecycle-unknown"


def test_case_insensitive_agreement():
    text = "**Lifecycle:** reference\n<!-- lifecycle: REFERENCE -->"
    errors, warnings = lifecycle.lifecycle_findings([("a.md", text)])
    assert errors == [] and warnings == []  # normalized to upper -> agree + known


def test_field_only_or_comment_only_is_accepted_if_valid():
    field_only = "**Lifecycle:** ACTIVE\n# Title"
    comment_only = "<!-- lifecycle: OPERATION -->\n# Title"
    e1, w1 = lifecycle.lifecycle_findings([("a.md", field_only)])
    e2, w2 = lifecycle.lifecycle_findings([("b.md", comment_only)])
    assert e1 == [] and w1 == [] and e2 == [] and w2 == []


def test_reference_is_a_known_state():
    assert "REFERENCE" in lifecycle.VALID_LIFECYCLE_STATES


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print("PASS", fn.__name__)
    print(f"--- ALL {len(fns)} PASS ---")
