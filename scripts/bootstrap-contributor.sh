#!/bin/sh
set -eu

: "${DOCPLANE_BOOTSTRAP_TOKEN:?set DOCPLANE_BOOTSTRAP_TOKEN}"

# Prefer an explicit URL. Otherwise derive a loopback URL from the deployment's
# published-port setting, including bind-qualified forms such as
# 127.0.0.1:18010. The production default is 18010, not the container's 8010.
API_URL=${DOCPLANE_API_URL:-}
if [ -z "$API_URL" ]; then
  PUBLISHED_PORT=${DOCPLANE_API_PORT:-127.0.0.1:18010}
  PORT=${PUBLISHED_PORT##*:}
  case "$PORT" in
    ''|*[!0-9]*)
      echo "DOCPLANE_API_PORT must be a port or bind-qualified port, got: $PUBLISHED_PORT" >&2
      exit 2
      ;;
  esac
  API_URL="http://127.0.0.1:${PORT}"
fi

DISPLAY_NAME=${1:-DocPlane Administrator}
KIND=${2:-HUMAN}
EXPIRY_SPEC=${3:-}

# Third argument accepts: never, 24h, 7d, or an RFC3339 timestamp. AGENT
# principals default to 24h; HUMAN and AUTOMATION remain explicit-policy
# credentials unless an expiry is supplied.
PAYLOAD=$(python3 - "$DISPLAY_NAME" "$KIND" "$EXPIRY_SPEC" <<'PY'
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone

name, kind, spec = sys.argv[1], sys.argv[2].upper(), sys.argv[3].strip()
if kind not in {"HUMAN", "AGENT", "AUTOMATION"}:
    raise SystemExit("principal kind must be HUMAN, AGENT or AUTOMATION")
if not spec and kind == "AGENT":
    spec = "24h"

expires_at = None
if spec and spec.lower() != "never":
    match = re.fullmatch(r"([1-9][0-9]*)([hd])", spec.lower())
    if match:
        value = int(match.group(1))
        delta = timedelta(hours=value) if match.group(2) == "h" else timedelta(days=value)
        expiry = datetime.now(timezone.utc) + delta
    else:
        normalized = spec.replace("Z", "+00:00")
        try:
            expiry = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise SystemExit("expiry must be never, <n>h, <n>d, or RFC3339") from exc
        if expiry.tzinfo is None:
            raise SystemExit("RFC3339 expiry must include a timezone")
        expiry = expiry.astimezone(timezone.utc)
    expires_at = expiry.isoformat().replace("+00:00", "Z")

payload = {
    "display_name": name,
    "principal_kind": kind,
    "metadata": {"issued_via": "scripts/bootstrap-contributor.sh"},
}
if expires_at is not None:
    payload["expires_at"] = expires_at
print(json.dumps(payload, separators=(",", ":")))
PY
)

curl -fsS \
  -X POST \
  -H "X-DocPlane-Bootstrap-Token: ${DOCPLANE_BOOTSTRAP_TOKEN}" \
  -H "Content-Type: application/json" \
  "${API_URL}/api/v1/bootstrap/principals" \
  -d "$PAYLOAD"
echo
