"""MkDocs build hooks.

on_post_build copies every markdown source into site/raw/ so agents (and the
"download as markdown" button in overrides/main.html) can fetch page sources
over plain HTTP — nginx serves them with text/markdown (see nginx.conf).

"Last updated" stamps are injected by docs-api from the database timestamps
(see docs-api/app/generator.py _augment_content), so no git plumbing is
needed here.
"""
import shutil
from pathlib import Path


def on_post_build(config, **kwargs):
    """Copy raw markdown files to site/raw/ after build."""
    docs_dir = Path(config["docs_dir"])
    site_dir = Path(config["site_dir"])
    raw_dir = site_dir / "raw"

    raw_dir.mkdir(parents=True, exist_ok=True)

    for md_file in docs_dir.rglob("*.md"):
        rel_path = md_file.relative_to(docs_dir)
        dest = raw_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(md_file, dest)

    print(f"Copied raw markdown files to {raw_dir}")
