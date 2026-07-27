"""Regression cover for the generated MkDocs release.

These tests exist because DocPlane shipped a generator that could never build a
site under its own documented Compose layout. `fresh-instance / verify` built
the images and exercised the API contract, but never once ran an actual mkdocs
build against a generated child config, so the failure only surfaced at the
first real publication:

    Config value 'theme': The path set in custom_dir
    ('/data/mkdocs/overrides') does not exist.

The fault line is that MkDocs resolves *relative* config paths against the
directory of the config file it was invoked with. DocPlane invokes it with the
derived child config in MKDOCS_CONFIG_DIR while the hand-maintained parent
(and everything it references relatively) lives beside MKDOCS_BASE_CONFIG.

Anything that asserts the release builds MUST therefore build with those two
directories split, exactly as the shipped compose does:

    MKDOCS_BASE_CONFIG=/app/mkdocs/mkdocs.yml
    MKDOCS_CONFIG_DIR=/data/mkdocs
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from app import generator

ROOT = Path(__file__).resolve().parents[2]
REPO_MKDOCS = ROOT / "mkdocs"


def _split_layout(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """Recreate the shipped layout: base config and config dir in DIFFERENT dirs."""
    base_dir = tmp_path / "app-mkdocs"      # stands in for /app/mkdocs (image)
    config_dir = tmp_path / "data-mkdocs"   # stands in for /data/mkdocs (volume)
    content_dir = tmp_path / "data-docs"    # stands in for /data/docs (volume)
    shutil.copytree(REPO_MKDOCS, base_dir)
    config_dir.mkdir()
    content_dir.mkdir()
    (content_dir / "index.md").write_text("# Home\n\nbody\n", encoding="utf-8")

    monkeypatch.setattr(generator, "MKDOCS_YML", base_dir / "mkdocs.yml")
    monkeypatch.setattr(generator, "MKDOCS_CONFIG_DIR", config_dir)
    monkeypatch.setattr(generator, "MKDOCS_NAV_YML", config_dir / "mkdocs.nav.yml")
    monkeypatch.setattr(generator, "DOCS_CONTENT_DIR", content_dir)
    return config_dir, content_dir


def test_shipped_base_config_still_uses_relative_asset_paths():
    """Guard the premise. If these ever become absolute this cover is moot."""
    raw = (REPO_MKDOCS / "mkdocs.yml").read_text(encoding="utf-8")
    assert "custom_dir: overrides" in raw
    assert "- hooks.py" in raw


def test_generated_child_config_pins_an_absolute_docs_dir(tmp_path, monkeypatch):
    """docs_dir must never be left to MkDocs' config-relative default."""
    config_dir, content_dir = _split_layout(tmp_path, monkeypatch)
    generator._write_nav([{"Home": "index.md"}])
    payload = yaml.safe_load((config_dir / "mkdocs.nav.yml").read_text(encoding="utf-8"))
    assert Path(payload["docs_dir"]).is_absolute()
    assert Path(payload["docs_dir"]) == content_dir


def test_base_config_assets_are_mirrored_beside_the_child_config(tmp_path, monkeypatch):
    """theme.custom_dir and hooks are relative; they must resolve from the child."""
    config_dir, _ = _split_layout(tmp_path, monkeypatch)
    generator._sync_base_config_assets()
    assert (config_dir / "overrides" / "main.html").is_file()
    assert (config_dir / "hooks.py").is_file()
    # The parent config itself is never copied - INHERIT already points at it.
    assert not (config_dir / "mkdocs.yml").exists()


def test_asset_sync_is_a_no_op_when_both_configs_share_a_directory(tmp_path, monkeypatch):
    base_dir = tmp_path / "mkdocs"
    shutil.copytree(REPO_MKDOCS, base_dir)
    monkeypatch.setattr(generator, "MKDOCS_YML", base_dir / "mkdocs.yml")
    monkeypatch.setattr(generator, "MKDOCS_CONFIG_DIR", base_dir)
    monkeypatch.setattr(generator, "MKDOCS_NAV_YML", base_dir / "mkdocs.nav.yml")
    before = sorted(p.name for p in base_dir.iterdir())
    generator._sync_base_config_assets()
    assert sorted(p.name for p in base_dir.iterdir()) == before


@pytest.mark.skipif(shutil.which("mkdocs") is None, reason="mkdocs not installed")
def test_release_actually_builds_with_split_config_directories(tmp_path, monkeypatch):
    """End-to-end guard: the exact failure that reached production.

    Without the fix this aborts on custom_dir, then on docs_dir. It is the only
    test here that would have caught the defect on its own.
    """
    config_dir, _ = _split_layout(tmp_path, monkeypatch)
    generator._write_nav([{"Home": "index.md"}])
    generator._sync_base_config_assets()

    site_dir = tmp_path / "site"
    build = subprocess.run(
        ["mkdocs", "build", "--config-file", str(config_dir / "mkdocs.nav.yml"),
         "--clean", "--site-dir", str(site_dir)],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr[-2000:]
    assert (site_dir / "index.html").is_file()
    # hooks.py ran (it copies raw markdown) => the relative hook resolved.
    assert (site_dir / "raw" / "index.md").is_file()
