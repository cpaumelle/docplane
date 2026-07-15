"""_write_nav_config — the generated nav is written to a SEPARATE git-ignored child
config (mkdocs.nav.yml), not spliced into the git-tracked mkdocs.yml.

This is the fix for the recurring mkdocs.yml nav drift: the docs-api used to regenerate
a ~560-line nav block inside the tracked mkdocs.yml on every deploy, so the pull-only
deployed tree drifted constantly. Now the volatile nav lives in mkdocs.nav.yml
(INHERIT: mkdocs.yml + nav), which is git-ignored, and mkdocs.yml stays clean.

Pure — imports app/generator.py directly (stdlib + yaml, no DB/mkdocs needed).
Run:  python3 -m pytest docs-api/tests/test_nav_config.py -q
"""
import importlib.util
import os
import tempfile

import yaml

_spec = importlib.util.spec_from_file_location(
    "generator", os.path.join(os.path.dirname(__file__), "..", "app", "generator.py"))
generator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generator)


_NAV_BLOCK = [
    {"Home": "index.md"},
    {"Getting Started": [{"Index": "getting-started/index.md"}]},
    {"Second": "second.md"},
]


def _write_to_tmp(nav_block):
    d = tempfile.mkdtemp(prefix="navcfg-")
    generator.MKDOCS_NAV_YML = os.path.join(d, "mkdocs.nav.yml")
    generator._write_nav_config(nav_block)
    with open(generator.MKDOCS_NAV_YML) as f:
        return d, f.read()


def test_targets_the_child_file_not_mkdocs_yml():
    # The whole point: the writer must NOT touch the tracked mkdocs.yml.
    assert generator.MKDOCS_NAV_YML != generator.MKDOCS_YML
    assert generator.MKDOCS_NAV_YML.endswith("mkdocs.nav.yml")


def test_output_is_inherit_plus_nav_and_round_trips():
    _d, text = _write_to_tmp(_NAV_BLOCK)
    doc = yaml.safe_load(text)               # no python/name tags in the nav file → safe_load OK
    assert doc["INHERIT"] == "mkdocs.yml"    # child inherits all other config from the parent
    assert doc["nav"] == _NAV_BLOCK          # nav preserved exactly, including nesting/order
    assert set(doc) == {"INHERIT", "nav"}    # nothing else leaks into the child


def test_has_generated_do_not_edit_header():
    _d, text = _write_to_tmp(_NAV_BLOCK)
    assert text.lstrip().startswith("#")
    assert "GENERATED" in text and "DO NOT EDIT" in text
    # header must be comments only — the first non-comment line is the INHERIT key
    first_code = next(l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#"))
    assert first_code.startswith("INHERIT:")


def test_write_is_atomic_no_tmp_left_behind():
    d, _text = _write_to_tmp(_NAV_BLOCK)
    assert os.path.exists(generator.MKDOCS_NAV_YML)
    assert not os.path.exists(generator.MKDOCS_NAV_YML + ".tmp")
    assert os.listdir(d) == ["mkdocs.nav.yml"]
