#!/usr/bin/env bash
# Fresh-install integration test — real containers, named volumes, empty start.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO_ROOT"

PROJECT=${PROJECT:-docplane_itest_$$}
API_PORT=${API_PORT:-18110}
DASH_PORT=${DASH_PORT:-18151}
SITE_PORT=${SITE_PORT:-18180}
MCP_PORT=${MCP_PORT:-18149}
ENV_FILE=$(mktemp)
API="http://127.0.0.1:${API_PORT}"
FRONT="http://127.0.0.1:${SITE_PORT}"
MCP="http://127.0.0.1:${MCP_PORT}/mcp"

log()  { printf '\n=== %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
compose() { docker compose -p "$PROJECT" --env-file "$ENV_FILE" "$@"; }
cleanup() {
  log "teardown (disposable project only)"
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -f "$ENV_FILE"
}
trap cleanup EXIT
rand() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

cat > "$ENV_FILE" <<EOF
POSTGRES_PASSWORD=$(rand)
DOCPLANE_BOOTSTRAP_TOKEN=$(rand)
DOCPLANE_EVENT_CURSOR_SECRET=$(rand)
MCP_API_KEY=$(rand)
DOCPLANE_TOKEN=
DOCPLANE_API_PORT=127.0.0.1:${API_PORT}
DOCPLANE_DASHBOARD_PORT=127.0.0.1:${DASH_PORT}
DOCPLANE_SITE_PORT=127.0.0.1:${SITE_PORT}
DOCPLANE_MCP_PORT=127.0.0.1:${MCP_PORT}
GIT_SHA=integration-test
BUILD_TIMESTAMP=integration-test
EOF
chmod 600 "$ENV_FILE"
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

log "1. start from COMPLETELY EMPTY volumes (project $PROJECT)"
compose down -v --remove-orphans >/dev/null 2>&1 || true
compose up --build -d postgres docs-api dashboard docs-web >/dev/null
for _ in $(seq 1 60); do
  curl -fsS "$API/healthz" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS "$API/healthz" >/dev/null || fail "docs-api never became healthy"

log "2. site-init ran and owns the docs-site volume"
compose ps -a --format '{{.Service}} {{.State}}' | grep -E '^site-init' || fail "site-init service missing"
OWNER=$(compose exec -T docs-api sh -c 'stat -c "%u:%g" /data/site')
[ "$OWNER" = "10001:10001" ] || fail "docs-site owned by $OWNER, expected 10001:10001"
compose exec -T docs-api sh -c 'test -w /data/site' || fail "docs-site not writable by docs-api"
compose exec -T docs-api sh -c 'test -f /data/site/.docplane-site-init' || fail "site-init sentinel missing"

log "3. bootstrap a contributor"
TOKEN=$(DOCPLANE_API_URL="$API" sh scripts/bootstrap-contributor.sh "Integration Test" HUMAN \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
[ -n "$TOKEN" ] || fail "no contributor token issued"
AUTH=(-H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json')

log "4. publish the FIRST page"
CID=$(curl -fsS -X POST "${AUTH[@]}" -H 'Idempotency-Key: itest-change' "$API/api/v1/changes" \
  -d '{"title":"Integration","purpose":"fresh-install integration test","workspace_key":"work"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["change_id"])')
curl -fsS -X POST "${AUTH[@]}" -H 'Idempotency-Key: itest-op' "$API/api/v1/changes/$CID/operations" \
  -d '{"operation_type":"CREATE_PAGE","payload":{"path":"work/itest.md","title":"Integration Test","nav_path":"Test/Integration","content":"# Integration Test\n\nSynthetic fresh-install probe.","workspace_key":"work"}}' >/dev/null
curl -fsS -X POST "${AUTH[@]}" "$API/api/v1/changes/$CID/validate" -d '{}' >/dev/null
PUB=$(curl -fsS -X POST "${AUTH[@]}" -H 'Idempotency-Key: itest-publish' "$API/api/v1/changes/$CID/publish" -d '{}')
DEPLOY=$(printf '%s' "$PUB" | python3 -c 'import json,sys; print(json.load(sys.stdin)["publication_receipt"]["deployment"]["status"])')
[ "$DEPLOY" = "COMPLETED" ] || fail "first-release promotion did not COMPLETE"

log "5. certification CURRENT with matching identities"
CERT=$(curl -fsS "${AUTH[@]}" "$API/api/v1/certification/status")
python3 - "$CERT" <<'PY'
import json,sys
c=json.loads(sys.argv[1])
assert c["state"]=="CURRENT"
assert c["working_state_identity"]==c["deployed_state_identity"]
assert c["release_id"]
PY

log "6. generated site and complete routed human/agent surface"
curl -fsS "$FRONT/work/itest/" | grep -q "Synthetic fresh-install probe" || fail "generated page missing"
curl -fsS "$FRONT/dashboard/" >/dev/null || fail "dashboard service DNS route failed"
curl -fsS "$FRONT/assets/app.js" >/dev/null || fail "dashboard asset route failed"
curl -fsS "$FRONT/.well-known/docplane.json" | grep -q 'docplane-agent-discovery-v2' || fail "discovery not routed"
curl -fsS "$FRONT/openapi.json" | grep -q '"title":"DocPlane"' || fail "OpenAPI not routed"
curl -fsS "$FRONT/healthz" | grep -q '"page_counts"' || fail "authority health not routed"
NOAUTH=$(curl -s -o /dev/null -w '%{http_code}' "$FRONT/api/v1/capabilities")
[ "$NOAUTH" = "401" ] || fail "unauthenticated routed API returned $NOAUTH"
curl -fsS "${AUTH[@]}" "$FRONT/api/v1/pages?limit=1" | grep -q '"total"' || fail "authenticated routed API failed"

log "7. nginx welcome content cleaned and sentinel retained"
compose exec -T docs-api sh -c 'test ! -f /data/site/50x.html' || fail "nginx welcome content survived"
CODE=$(curl -s -o /dev/null -w '%{http_code}' "$FRONT/50x.html")
[ "$CODE" = "404" ] || fail "nginx welcome asset still served"
compose exec -T docs-api sh -c 'test -f /data/site/.docplane-site-init' || fail "sentinel deleted"

log "8. MCP on NON-DEFAULT port ${MCP_PORT}"
sed -i "s|^DOCPLANE_TOKEN=.*|DOCPLANE_TOKEN=${TOKEN}|" "$ENV_FILE"
export DOCPLANE_TOKEN="$TOKEN"
compose up --build -d docs-mcp >/dev/null
for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:${MCP_PORT}/healthz" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS "http://127.0.0.1:${MCP_PORT}/healthz" | grep -q '"contributor_token_configured":true' || fail "MCP token missing"
MH=(-H "Authorization: Bearer $MCP_API_KEY" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream')
INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"itest","version":"1"}}}'
SID=$(curl -s -D - -o /dev/null -X POST "$MCP" "${MH[@]}" -d "$INIT" | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}')
[ -n "$SID" ] || fail "MCP handshake failed"
curl -s -X POST "$MCP" "${MH[@]}" -H "Mcp-Session-Id: $SID" -H 'MCP-Protocol-Version: 2025-06-18' -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
READ=$(curl -s -X POST "$MCP" "${MH[@]}" -H "Mcp-Session-Id: $SID" -H 'MCP-Protocol-Version: 2025-06-18' -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"read_doc","arguments":{"path_or_slug":"work/itest.md"}}}' | sed -n 's/^data: //p')
printf '%s' "$READ" | grep -q 'work/itest.md' || fail "MCP read_doc failed"
BAD=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$MCP" -H 'Authorization: Bearer wrong-key' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$INIT")
[ "$BAD" = "401" ] || fail "invalid MCP key returned $BAD"

log "9. routed mutation forwards auth/idempotency and waits for publication"
compose up -d --force-recreate site-init >/dev/null 2>&1 || true
compose exec -T docs-api sh -c 'test -w /data/site' || fail "site-init not idempotent"
RETRY=$(curl -fsS -X POST "${AUTH[@]}" -H 'Idempotency-Key: itest-retry' "$FRONT/api/v1/publication/retry" -d '{}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
[ "$RETRY" = "COMPLETED" ] || fail "routed publication retry returned $RETRY"

log "FRESH-INSTALL INTEGRATION TEST PASSED"
