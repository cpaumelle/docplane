#!/bin/sh
set -eu

: "${DOCPLANE_BOOTSTRAP_TOKEN:?set DOCPLANE_BOOTSTRAP_TOKEN}"
API_URL=${DOCPLANE_API_URL:-http://localhost:8010}
DISPLAY_NAME=${1:-DocPlane Administrator}
KIND=${2:-HUMAN}

curl -fsS \
  -X POST \
  -H "X-DocPlane-Bootstrap-Token: ${DOCPLANE_BOOTSTRAP_TOKEN}" \
  -H "Content-Type: application/json" \
  "${API_URL}/api/v1/bootstrap/principals" \
  -d "$(python -c 'import json,sys; print(json.dumps({"display_name":sys.argv[1],"principal_kind":sys.argv[2]}))' "$DISPLAY_NAME" "$KIND")"
echo
