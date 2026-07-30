"""Shared DocPlane MCP configuration."""
from __future__ import annotations

import os

DOCPLANE_API_URL = os.environ.get("DOCPLANE_API_URL", "http://docs-api:8010").rstrip("/")
DOCPLANE_TOKEN = os.environ.get("DOCPLANE_TOKEN", "")
MCP_SERVER_NAME = os.environ.get("MCP_SERVER_NAME", "docplane")
# Optional allowlisted directory for write_doc's content_path variant
# (docplane#88): large documents are read by the MCP server itself instead of
# transiting the calling agent's context. Empty = the variant is disabled.
DOCPLANE_MCP_CONTENT_DIR = os.environ.get("DOCPLANE_MCP_CONTENT_DIR", "")
