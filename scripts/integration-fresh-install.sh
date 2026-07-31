#!/usr/bin/env bash
#
# Fresh-install integration test — real containers, real named volumes, empty start.
#
# This exercises the container/volume boundary and the complete cold-agent route:
# discovery → trusted-front self-issue → authenticated publication, without
# bootstrap material. Direct docs-api reachability alone must not admit issuance.
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
  rm -f branding/brand.css branding/logo.svg branding/favicon.svg
  rm -f "$ENV_FILE"
}
trap cleanup EXIT

rand() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

cat > "$ENV_FILE" <<EOF
POSTGRES_PASSWORD=$(rand)
DOCPLANE_BOOTSTRAP_TOKEN=
DOCPLANE_EVENT_CURSOR_SECRET=$(rand)
MCP_API_KEY=$(rand)
DOCPLANE_TOKEN=
DOCPLANE_ACCESS_PROFILE=private_fabric
DOCPLANE_SELF_ISSUE_TTL_SECONDS=3600
DOCPLANE_SELF_ISSUE_SOURCE_BURST_LIMIT=1
DOCPLANE_SELF_ISSUE_SOURCE_BURST_WINDOW_SECONDS=60
DOCPLANE_SELF_ISSUE_SOURCE_LIMIT_PER_HOUR=30
DOCPLANE_SELF_ISSUE_GLOBAL_LIMIT_PER_HOUR=10
DOCPLANE_API_PORT=127.0.0.1:${API_PORT}
DOCPLANE_DASHBOARD_PORT=127.0.0.1:${DASH_PORT}
DOCPLANE_SITE_PORT=127.0.0.1:${SITE_PORT}
DOCPLANE_MCP_PORT=127.0.0.1:${MCP_PORT}
DOCPLANE_SITE_NAME='Integration DocPlane'
DOCPLANE_SITE_URL=
GIT_SHA=integration-test
BUILD_TIMESTAMP=integration-test
EOF
chmod 600 "$ENV_FILE"
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

log "1. start from COMPLETELY EMPTY volumes (project $PROJECT)"
compose down -v --remove-orphans >/dev/null 2>&1 || true
compose up --build -d postgres docs-api dashboard docs-web >/dev/null
for _ in $(seq 1 60); do curl -fsS "$API/healthz" >/dev/null 2>&1 && break; sleep 2; done
curl -fsS "$API/healthz" >/dev/null || fail "docs-api never became healthy"
# Waiting on the ROUTED dashboard health covers both nginx accepting
# connections and the dashboard being up, so step 2 cannot race the front.
for _ in $(seq 1 30); do curl -fsS "$FRONT/dashboard/healthz" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "$FRONT/dashboard/healthz" >/dev/null || fail "routed dashboard never became healthy"

log "2. routed HEAD and Link discovery work before any credential exists"
HEADERS=$(mktemp)
curl -fsSI "$FRONT/" > "$HEADERS" || fail "HEAD / failed"
grep -qi '^Link: </.well-known/docplane.json>; rel="describedby"; type="application/json"' "$HEADERS" || fail "root response did not advertise discovery"
rm -f "$HEADERS"
curl -fsSI "$FRONT/.well-known/docplane.json" >/dev/null || fail "HEAD discovery failed"
curl -fsSI "$FRONT/healthz" >/dev/null || fail "HEAD health failed"
NOAUTH_HEAD=$(curl -s -o /dev/null -w '%{http_code}' -I "$FRONT/api/v1/pages")
[ "$NOAUTH_HEAD" = "401" ] || fail "unauthenticated HEAD pages returned $NOAUTH_HEAD"

log "3. direct API cannot satisfy private-fabric admission"
DIRECT=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' "$API/api/v1/auth/self-issue" -d '{"display_name":"direct-agent"}')
[ "$DIRECT" = "403" ] || fail "direct self-issue returned $DIRECT, expected 403"

log "4. dashboard discovers and automatically self-issues with no bootstrap secret"
DISCOVERY=$(curl -fsS "$FRONT/.well-known/docplane.json")
python3 - "$DISCOVERY" <<'PY'
import json,sys
body=json.loads(sys.argv[1])
assert body["contract_version"] == "docplane-agent-discovery-v3"
a=body["authentication"]["token_acquisition"]
assert a["access_profile"] == "private_fabric"
assert a["self_service"] is True
assert a["requires_existing_credential"] is False
assert a["endpoint"] == "/api/v1/auth/self-issue"
assert body["site_name"] == "Integration DocPlane"
PY
BOOTSTRAP=$(FRONT="$FRONT" node <<'JS'
require("./dashboard/static/app.js");
const {createAuthentication, TOKEN_KEY} = globalThis.DocPlaneAuth;
class Storage {
  constructor() { this.values = new Map(); }
  getItem(key) { return this.values.get(key) || null; }
  setItem(key, value) { this.values.set(key, value); }
  removeItem(key) { this.values.delete(key); }
}
const storage = new Storage();
let issued = null;
let capability = null;
let acquisitionEndpoint = null;
let lastState = null;
const calls = [];
const request = async (path, options = {}) => {
  calls.push(path);
  const response = await fetch(new URL(path, process.env.FRONT), options);
  const payload = await response.json();
  if (!response.ok) throw Object.assign(new Error(payload.detail?.code || `HTTP ${response.status}`), {status: response.status});
  if (path === "/api/control-plane/discovery") acquisitionEndpoint = payload.authentication?.token_acquisition?.endpoint;
  if (path === "/api/v1/auth/self-issue") issued = payload;
  return payload;
};
(async () => {
  const auth = createAuthentication({
    request,
    storage,
    onState: (state, detail) => { lastState = {state, message: detail?.message, error: detail?.error?.message, status: detail?.error?.status}; },
    onConnected: (value) => { capability = value; },
  });
  if (!await auth.initialize()) throw new Error(`dashboard bootstrap did not connect: ${JSON.stringify(lastState)}`);
  const overview = await request("/api/control-plane/overview", {
    headers: {Authorization: `Bearer ${auth.token()}`},
  });
  const principal = capability?.principal || {};
  if (principal.principal_kind !== "AGENT" || principal.role !== "CONTRIBUTOR") throw new Error("wrong dashboard principal");
  if (issued?.access_profile !== "private_fabric" || issued?.issued_via !== "fabric_reachability" || !issued?.expires_at) throw new Error("wrong issuance provenance");
  if (typeof overview?.modules?.structure?.data?.summary?.active_pages !== "number") throw new Error("dashboard overview did not return corpus values");
  if (!acquisitionEndpoint || calls.filter((path) => path === acquisitionEndpoint).length !== 1) throw new Error("dashboard did not issue exactly once through discovery");
  process.stdout.write(JSON.stringify({token: storage.getItem(TOKEN_KEY), principal, issued, active_pages: overview.modules.structure.data.summary.active_pages}));
})().catch((error) => { console.error(error); process.exit(1); });
JS
)
TOKEN=$(printf '%s' "$BOOTSTRAP" | python3 -c 'import json,sys; b=json.load(sys.stdin); assert b["principal"]["principal_kind"]=="AGENT" and b["principal"]["role"]=="CONTRIBUTOR"; assert b["issued"]["access_profile"]=="private_fabric" and b["issued"]["issued_via"]=="fabric_reachability" and b["issued"]["expires_at"]; print(b["token"])')
[ -n "$TOKEN" ] || fail "self-issue returned no token"
RATE_BODY=$(mktemp)
RATE_HEADERS=$(mktemp)
SECOND=$(curl -s -D "$RATE_HEADERS" -o "$RATE_BODY" -w '%{http_code}' -X POST -H 'Content-Type: application/json' "$FRONT/api/v1/auth/self-issue" -d '{"display_name":"duplicate-agent"}')
[ "$SECOND" = "429" ] || fail "per-source issuance limit returned $SECOND, expected 429"
python3 - "$RATE_BODY" "$RATE_HEADERS" <<'PY'
import json, pathlib, sys
body = json.loads(pathlib.Path(sys.argv[1]).read_text())["detail"]
headers = pathlib.Path(sys.argv[2]).read_text().splitlines()
retry_header = next(
    line.split(":", 1)[1].strip()
    for line in headers
    if line.lower().startswith("retry-after:")
)
assert body["code"] == "SELF_ISSUE_RATE_LIMITED"
assert body["scope"] == "source_burst"
assert body["limit"] == 1
assert body["window_seconds"] == 60
assert 1 <= body["retry_after_seconds"] <= 60
assert retry_header == str(body["retry_after_seconds"])
PY
rm -f "$RATE_BODY" "$RATE_HEADERS"
AUTH=(-H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json')
curl -fsSI -H "Authorization: Bearer $TOKEN" "$FRONT/api/v1/pages?limit=1" >/dev/null || fail "authenticated HEAD pages failed"

log "5. site-init ran and owns the docs-site volume"
compose ps -a --format '{{.Service}} {{.State}}' | grep -E '^site-init' || fail "site-init service missing"
OWNER=$(compose exec -T docs-api sh -c 'stat -c "%u:%g" /data/site')
[ "$OWNER" = "10001:10001" ] || fail "docs-site owned by $OWNER, expected 10001:10001"
compose exec -T docs-api sh -c 'test -w /data/site && test -f /data/site/.docplane-site-init' || fail "site volume not ready"

log "6. self-issued agent publishes the FIRST page"
CID=$(curl -fsS -X POST "${AUTH[@]}" -H 'Idempotency-Key: itest-change' "$API/api/v1/changes" -d '{"title":"Integration","purpose":"fresh-install integration test","workspace_key":"work"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["change_id"])')
curl -fsS -X POST "${AUTH[@]}" -H 'Idempotency-Key: itest-op' "$API/api/v1/changes/$CID/operations" -d '{"operation_type":"CREATE_PAGE","payload":{"path":"work/itest.md","title":"Integration Test","nav_path":"Test/Integration","content":"# Integration Test\n\nSynthetic fresh-install probe.","workspace_key":"work"}}' >/dev/null
curl -fsS -X POST "${AUTH[@]}" "$API/api/v1/changes/$CID/validate" -d '{}' >/dev/null
PUB=$(curl -fsS -X POST "${AUTH[@]}" -H 'Idempotency-Key: itest-publish' "$API/api/v1/changes/$CID/publish" -d '{}')
DEPLOY=$(printf '%s' "$PUB" | python3 -c 'import json,sys; print(json.load(sys.stdin)["publication_receipt"]["deployment"]["status"])')
[ "$DEPLOY" = "COMPLETED" ] || fail "first-release promotion did not COMPLETE"

log "7. certification CURRENT with matching identities"
CERT=$(curl -fsS "${AUTH[@]}" "$API/api/v1/certification/status")
python3 - "$CERT" <<'PY'
import json,sys
c=json.loads(sys.argv[1]); assert c["state"]=="CURRENT"; assert c["working_state_identity"]==c["deployed_state_identity"]; assert c["release_id"]
PY

log "8. generated site serves the page with configured identity and HTML discovery"
HTML=$(curl -fsS "$FRONT/work/itest/")
printf '%s' "$HTML" | grep -q "Synthetic fresh-install probe" || fail "generated page missing expected content"
printf '%s' "$HTML" | grep -q "Integration DocPlane" || fail "DOCPLANE_SITE_NAME did not reach MkDocs"
printf '%s' "$HTML" | grep -q 'rel="describedby" href="/.well-known/docplane.json"' || fail "HTML discovery link missing"

log "9. routed human and agent surfaces"
curl -fsS "$FRONT/dashboard/" >/dev/null || fail "dashboard route failed"
curl -fsS "$FRONT/openapi.json" | grep -q '"DocPlaneBearer"' || fail "OpenAPI bearer scheme is not routed"
curl -fsS "$FRONT/healthz" | grep -q '"page_counts"' || fail "authority health is not routed"
NOAUTH=$(curl -s -o /dev/null -w '%{http_code}' "$FRONT/api/v1/capabilities")
[ "$NOAUTH" = "401" ] || fail "unauthenticated routed API returned $NOAUTH"
curl -fsS "${AUTH[@]}" "$FRONT/api/v1/pages?limit=1" | grep -q '"total"' || fail "authenticated routed API failed"

log "10. neutral brand fallback and local overlay both serve"
BASE="$FRONT/assets"
curl -fsS "$BASE/brand.css" | grep -q "brand-override slot" || fail "neutral brand.css fallback failed"
printf '%s\n' ':root{--brand-500:#123456} /* integration-overlay */' > branding/brand.css
curl -fsS "$BASE/brand.css" | grep -q "integration-overlay" || fail "brand.css overlay failed"
rm -f branding/brand.css
curl -fsS "$BASE/brand.css" | grep -q "brand-override slot" || fail "neutral fallback did not resume"

log "11. generated-site ownership invariants"
compose exec -T docs-api sh -c 'test ! -f /data/site/50x.html && test -f /data/site/.docplane-site-init' || fail "site invariants failed"

log "12. MCP on non-default port"
sed -i "s|^DOCPLANE_TOKEN=.*|DOCPLANE_TOKEN=${TOKEN}|" "$ENV_FILE"
export DOCPLANE_TOKEN="$TOKEN"
compose up --build -d docs-mcp >/dev/null
for _ in $(seq 1 30); do curl -fsS "http://127.0.0.1:${MCP_PORT}/healthz" >/dev/null 2>&1 && break; sleep 2; done
curl -fsS "http://127.0.0.1:${MCP_PORT}/healthz" | grep -q '"contributor_token_configured":true' || fail "MCP token missing"

log "13. routed publication retry"
compose up -d --force-recreate site-init >/dev/null 2>&1 || true
RETRY=$(curl -fsS -X POST "${AUTH[@]}" -H 'Idempotency-Key: itest-retry' "$FRONT/api/v1/publication/retry" -d '{}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
[ "$RETRY" = "COMPLETED" ] || fail "routed publication retry returned $RETRY"

log "14. knowledge-class suggest -> curate -> apply converges"
CLASS_CHANGE=$(curl -fsS -X POST "${AUTH[@]}" -H 'Idempotency-Key: itest-class-change' "$API/api/v1/changes" -d '{"title":"Classification seed","purpose":"fresh-install knowledge-class round trip","workspace_key":"reference"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["change_id"])')
curl -fsS -X POST "${AUTH[@]}" -H 'Idempotency-Key: itest-class-op' "$API/api/v1/changes/$CLASS_CHANGE/operations" -d '{"operation_type":"CREATE_PAGE","payload":{"path":"operations/itest-classify.md","title":"Classification Integration","nav_path":"Operations/Classification integration","content":"# Classification Integration\n\n## Preconditions\n\nReady.\n\n## Procedure\n\nApply.\n\n## Rollback\n\nRevert.\n","workspace_key":"reference"}}' >/dev/null
curl -fsS -X POST "${AUTH[@]}" "$API/api/v1/changes/$CLASS_CHANGE/validate" -d '{}' >/dev/null
curl -fsS -X POST "${AUTH[@]}" -H 'Idempotency-Key: itest-class-publish' "$API/api/v1/changes/$CLASS_CHANGE/publish" -d '{}' >/dev/null
SUGGESTIONS=$(mktemp)
CURATED=$(mktemp)
DOCPLANE_API="$API" DOCPLANE_TOKEN="$TOKEN" python3 scripts/knowledge_class_suggest.py --out "$SUGGESTIONS" >/dev/null
python3 - "$SUGGESTIONS" "$CURATED" <<'PY'
import json,pathlib,sys
report=json.loads(pathlib.Path(sys.argv[1]).read_text())
report["suggestions"]=[item for item in report["suggestions"] if item["path"]=="operations/itest-classify.md"]
assert len(report["suggestions"])==1 and report["suggestions"][0]["proposed"]=="OPERATION"
pathlib.Path(sys.argv[2]).write_text(json.dumps(report))
PY
FIRST_APPLY=$(DOCPLANE_API="$API" DOCPLANE_TOKEN="$TOKEN" python3 scripts/knowledge_class_apply.py "$CURATED")
SECOND_APPLY=$(DOCPLANE_API="$API" DOCPLANE_TOKEN="$TOKEN" python3 scripts/knowledge_class_apply.py "$CURATED")
python3 - "$FIRST_APPLY" "$SECOND_APPLY" <<'PY'
import json,sys
first,second=map(json.loads,sys.argv[1:])
assert first["counts"]=={"applied":1,"already_matched":0,"optimistic_lock_skips":0,"coherence_refusals":0,"remaining":0}
assert second["counts"]=={"applied":0,"already_matched":1,"optimistic_lock_skips":0,"coherence_refusals":0,"remaining":0}
PY
rm -f "$SUGGESTIONS" "$CURATED"
compose exec -T postgres psql -U docs -d docs -Atc "SELECT count(*) FROM pg_constraint WHERE conname='pages_knowledge_class_check'" | grep -qx 1 || fail "migration 010 CHECK missing"
curl -fsS "${AUTH[@]}" "$API/api/v1/pages?path=operations/itest-classify.md" | grep -q '"knowledge_class":"OPERATION"' || fail "curated class was not applied"

log "15. managed routed front does not self-issue"
compose down -v --remove-orphans >/dev/null
sed -i 's/^DOCPLANE_ACCESS_PROFILE=.*/DOCPLANE_ACCESS_PROFILE=managed/' "$ENV_FILE"
export DOCPLANE_ACCESS_PROFILE=managed
compose up -d postgres docs-api dashboard docs-web >/dev/null
for _ in $(seq 1 60); do curl -fsS "$API/healthz" >/dev/null 2>&1 && break; sleep 2; done
for _ in $(seq 1 30); do curl -fsS "$FRONT/dashboard/healthz" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "$FRONT/dashboard/healthz" >/dev/null || fail "managed routed dashboard never became healthy"
FRONT="$FRONT" node <<'JS'
require("./dashboard/static/app.js");
const {createAuthentication} = globalThis.DocPlaneAuth;
const values = new Map();
const storage = {getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key)};
const calls = [];
const states = [];
const request = async (path, options = {}) => {
  calls.push(path);
  const response = await fetch(new URL(path, process.env.FRONT), options);
  const payload = await response.json();
  if (!response.ok) throw Object.assign(new Error(payload.detail?.code || `HTTP ${response.status}`), {status: response.status});
  return payload;
};
(async () => {
  const auth = createAuthentication({request, storage, onState: (state, detail) => states.push({state, detail})});
  if (await auth.initialize()) throw new Error("managed dashboard unexpectedly connected");
  if (calls.some((path) => path.includes("self-issue"))) throw new Error("managed dashboard attempted self-issue");
  if (auth.state() !== "managed-token-required") throw new Error(`wrong managed state: ${auth.state()}`);
  if (!states.at(-1).detail.procedure) throw new Error("managed operator procedure missing");
})().catch((error) => { console.error(error); process.exit(1); });
JS

log "FRESH-INSTALL INTEGRATION TEST PASSED"
