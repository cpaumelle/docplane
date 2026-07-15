#!/usr/bin/env python3
"""
One-time migration: imports existing .md files from the docs filesystem into the DB.

Run from hub2:
  docker run --rm \
    --network service-net \
    -e DB_HOST=charliehub-postgres \
    -e DB_NAME=charliehub \
    -e DB_USER=charliehub \
    -e DB_PASS=<password> \
    -v /opt/charliehub/docs-mkdocs:/docs-mkdocs:ro \
    charliehub_docs_api \
    python scripts/migrate.py

Or directly on hub2:
  DB_HOST=... DB_NAME=... DB_USER=... DB_PASS=... python scripts/migrate.py
"""

import os
import re
import sys

import psycopg2
import yaml

DOCS_ROOT = os.environ.get("DOCS_ROOT", "/docs-mkdocs/docs")
MKDOCS_YML = os.environ.get("MKDOCS_YML", "/docs-mkdocs/mkdocs.yml")


def connect():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
    )


def load_nav_map(mkdocs_yml: str) -> dict:
    """Parse mkdocs.yml nav: section into {file_path: nav_path} mapping."""
    nav_map = {}
    try:
        with open(mkdocs_yml) as f:
            config = yaml.safe_load(f)
        nav = config.get("nav", [])
        _walk_nav(nav, [], nav_map)
    except Exception as e:
        print(f"Warning: could not parse mkdocs.yml nav: {e}", file=sys.stderr)
    return nav_map


def _walk_nav(nav_items, section_stack: list, nav_map: dict):
    for item in nav_items:
        if isinstance(item, dict):
            for key, value in item.items():
                if isinstance(value, str):
                    # Leaf: key is the title, value is the file path
                    nav_path = "/".join(section_stack + [key]) if section_stack else key
                    # Normalise to exactly one slash (truncate deeper nesting)
                    parts = nav_path.split("/")
                    if len(parts) == 1:
                        nav_path = f"Docs/{parts[0]}"
                    elif len(parts) >= 2:
                        nav_path = f"{parts[0]}/{'/'.join(parts[1:])}"
                    # nav_path constraint: exactly one slash
                    if nav_path.count("/") > 1:
                        nav_path = f"{parts[0]}/{' '.join(parts[1:])}"
                    nav_map[value] = nav_path
                elif isinstance(value, list):
                    _walk_nav(value, section_stack + [key], nav_map)


def infer_nav_path(rel_path: str) -> str:
    """Fallback: infer nav_path from directory/filename."""
    parts = rel_path.replace(".md", "").split("/")
    if len(parts) == 1:
        return f"Docs/{_humanise(parts[0])}"
    section = _humanise(parts[0])
    title = _humanise(parts[-1])
    return f"{section}/{title}"


def _humanise(slug: str) -> str:
    return re.sub(r"[-_]", " ", slug).title()


def main():
    nav_map = load_nav_map(MKDOCS_YML)
    print(f"Loaded {len(nav_map)} nav entries from mkdocs.yml")

    conn = connect()
    cur = conn.cursor()

    inserted = 0
    skipped = 0
    warnings = []

    for root, _, files in os.walk(DOCS_ROOT):
        for fname in sorted(files):
            if not fname.endswith(".md"):
                continue

            abs_path = os.path.join(root, fname)
            rel_path = os.path.relpath(abs_path, DOCS_ROOT)  # e.g. "agent-guides/api-guide.md"

            # Validate path constraint
            if not re.match(r'^[a-z0-9/_-]+\.md$', rel_path):
                warnings.append(f"Skip (invalid path chars): {rel_path}")
                continue

            with open(abs_path) as f:
                content = f.read()

            if not content.strip().startswith("#"):
                warnings.append(f"Skip (no H1): {rel_path}")
                continue

            # Derive title from first H1
            first_line = content.strip().splitlines()[0]
            title = first_line.lstrip("#").strip() or fname.replace(".md", "")

            # Derive nav_path
            nav_path = nav_map.get(rel_path) or infer_nav_path(rel_path)

            cur.execute(
                """
                INSERT INTO docs.pages (path, title, nav_path, content, updated_by)
                VALUES (%s, %s, %s, %s, 'migrate')
                ON CONFLICT (path) DO NOTHING
                """,
                (rel_path, title, nav_path, content),
            )
            if cur.rowcount:
                print(f"  inserted: {rel_path!r} → nav_path={nav_path!r}")
                inserted += 1
            else:
                skipped += 1

    conn.commit()
    conn.close()

    print(f"\nDone. inserted={inserted} skipped={skipped}")
    if warnings:
        print("\nWarnings:")
        for w in warnings:
            print(f"  {w}")


if __name__ == "__main__":
    main()
