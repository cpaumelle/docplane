"""Shared config helpers for docs-mcp tool modules."""
from __future__ import annotations

import os

DOCS_API_URL = os.environ.get("DOCS_API_URL", "http://docs-api:8010")


def pick(d: dict, fields: tuple) -> dict:
    return {k: d[k] for k in fields if k in d}
