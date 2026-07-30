"""Shared DocPlane MCP configuration."""
from __future__ import annotations

import os

DOCPLANE_API_URL = os.environ.get("DOCPLANE_API_URL", "http://docs-api:8010").rstrip("/")
DOCPLANE_TOKEN = os.environ.get("DOCPLANE_TOKEN", "")
MCP_SERVER_NAME = os.environ.get("MCP_SERVER_NAME", "docplane")
