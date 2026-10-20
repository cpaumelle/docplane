#!/usr/bin/env bash
# test_refresh.sh — end-to-end test of refresh.sh in a THROWAWAY dev project.
# Never touches the persistent instance: own project, ports, state dir and inbox,
# all removed at the end. Requires Docker; run from a committed DOCPLANE_SRC.
#
#   DOCPLANE_SRC=<checkout> bash scripts/dev/test_refresh.sh
set -euo pipefail
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export DOCPLANE_SRC="$(cd "${DOCPLANE_SRC:-$TOOLS/../..}" && pwd)"
export DOCPLANE_DEV_PROJECT="docplane-refreshtest-$$"
export DOCPLANE_DEV_HOME="$(mktemp -d)"
export DOCPLANE_DEV_API_PORT=29010 DOCPLANE_DEV_DASHBOARD_PORT=29051 DOCPLANE_DEV_SITE_PORT=29080 DOCPLANE_DEV_MCP_PORT=29049
export DOCPLANE_DEV_REFRESH_INBOX="$DOCPLANE_DEV_HOME/inbox"
D="$TOOLS/devstack.sh"; API="http://127.0.0.1:$DOCPLANE_DEV_API_PORT"
SRC_PG="${DOCPLANE_DEV_PROJECT}-source"
PASS=0; FAIL=0
ok() { if eval "$2"; then PASS=$((PASS+1)); echo "PASS  $1"; else FAIL=$((FAIL+1)); echo "FAIL  $1"; fi; }
psql_dev() { "$D" _compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$1" -At -q' _ "$1"; }
cleanup() {
  "$D" _compose down -v --remove-orphans --rmi local >/dev/null 2>&1 || true
  docker rm -f "$SRC_PG" >/dev/null 2>&1 || true
  rm -rf "$DOCPLANE_DEV_HOME"
}
trap cleanup EXIT
mkdir -p "$DOCPLANE_DEV_REFRESH_INBOX"

echo "== throwaway dev instance $DOCPLANE_DEV_PROJECT (synthetic)"
"$D" init >/dev/null; "$D" start >/dev/null 2>&1; "$D" provision >/dev/null
TOKEN="$(cat "$DOCPLANE_DEV_HOME/tokens/dev-mcp-work-evidence.token")"
auth() { curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $1" "$API/api/v1/work/captures?limit=1&status=all"; }
ok "dev token authenticates before refresh" '[ "$(auth "$TOKEN")" = 200 ]'

echo "== source database standing in for production (migrated from DOCPLANE_SRC, synthetic rows)"
docker run -d --name "$SRC_PG" -e POSTGRES_DB=docs -e POSTGRES_USER=docs -e POSTGRES_PASSWORD=x postgres:16-alpine >/dev/null
until docker exec "$SRC_PG" pg_isready -U docs -d docs >/dev/null 2>&1; do sleep 1; done
docker run --rm --network "container:$SRC_PG" -e DB_HOST=127.0.0.1 -e DB_PORT=5432 -e DB_NAME=docs -e DB_USER=docs -e DB_PASS=x \
  --entrypoint python "${DOCPLANE_DEV_PROJECT}-docs-api" /app/migrate.py apply --dir /app/db/migrations >/dev/null
PROD_TOKEN_SHAPE="dp_$(head -c 40 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 48)"
docker exec -i "$SRC_PG" psql -U docs -d docs -q -v ON_ERROR_STOP=1 >/dev/null <<SQL
INSERT INTO docplane.principals (principal_id, principal_kind, display_name) VALUES ('00000000-0000-4000-8000-0000000000a1', 'AUTOMATION', 'source-automation');
INSERT INTO docplane.api_tokens (principal_id, token_hash, token_prefix) VALUES ('00000000-0000-4000-8000-0000000000a1', repeat('a', 64), 'dp_sourcetok');
INSERT INTO docplane.mutation_receipts (principal_id, idempotency_key, operation_type, request_hash, response)
  VALUES ('00000000-0000-4000-8000-0000000000a1', 'k-cred', 'TEST', repeat('b', 64), jsonb_build_object('token', '$PROD_TOKEN_SHAPE')),
         ('00000000-0000-4000-8000-0000000000a1', 'k-plain', 'TEST', repeat('c', 64), '{"ok": true}');
INSERT INTO work.captures (body, kind, author_principal_id, idempotency_key) VALUES ('source capture', 'FINDING', '00000000-0000-4000-8000-0000000000a1', 'cap-1');
SQL

export_pair() {  # $1 = stamp; writes dump + manifest in the documented export format
  local d="$DOCPLANE_DEV_REFRESH_INBOX/docplane-$1.dump" m="$DOCPLANE_DEV_REFRESH_INBOX/docplane-$1.manifest" t
  docker exec "$SRC_PG" pg_dump -U docs -d docs -Fc --no-owner --no-privileges > "$d"
  { echo "dump_sha256=$(sha256sum "$d" | cut -d' ' -f1)"; echo "dump_bytes=$(stat -c %s "$d")"
    echo "source_git_sha=test-source"; echo "dumped_at=$1"
    for t in docplane.principals docs.pages work.captures work.initiatives work.initiative_activities docplane.mutation_receipts docplane.events; do
      echo "count_$t=$(docker exec "$SRC_PG" psql -U docs -d docs -At -c "SELECT count(*) FROM $t;")"; done; } > "$m"
}

echo "== 1. incomplete transfer (dump without manifest) is ignored"
docker exec "$SRC_PG" pg_dump -U docs -d docs -Fc > "$DOCPLANE_DEV_REFRESH_INBOX/docplane-20990101T000000Z.dump"
ok "refresh refuses with no verified pair" '! "$D" refresh run >/dev/null 2>&1'
rm -f "$DOCPLANE_DEV_REFRESH_INBOX"/*
export_pair 20990101T003000Z
chmod 000 "$DOCPLANE_DEV_REFRESH_INBOX"/docplane-20990101T003000Z.*
ok "unreadable pair is never treated as verified" '! "$D" refresh run >/dev/null 2>&1 && grep -q "no complete" "$DOCPLANE_DEV_HOME/refresh.status.json"'
chmod 600 "$DOCPLANE_DEV_REFRESH_INBOX"/*; rm -f "$DOCPLANE_DEV_REFRESH_INBOX"/*

echo "== 2. failed restore leaves the working copy"
export_pair 20990101T010000Z
head -c 2000 "$DOCPLANE_DEV_REFRESH_INBOX/docplane-20990101T010000Z.dump" > "$DOCPLANE_DEV_HOME/trunc"
mv "$DOCPLANE_DEV_HOME/trunc" "$DOCPLANE_DEV_REFRESH_INBOX/docplane-20990101T010000Z.dump"
sed -i "s/^dump_sha256=.*/dump_sha256=$(sha256sum "$DOCPLANE_DEV_REFRESH_INBOX/docplane-20990101T010000Z.dump" | cut -d' ' -f1)/" "$DOCPLANE_DEV_REFRESH_INBOX/docplane-20990101T010000Z.manifest"
ok "truncated dump refused" '! "$D" refresh run >/dev/null 2>&1'
ok "working copy untouched and dev token still works" '[ "$(auth "$TOKEN")" = 200 ] && [ -z "$(echo "SELECT 1 FROM pg_database WHERE datname = '"'"'docs_staging'"'"';" | psql_dev postgres)" ]'
rm -f "$DOCPLANE_DEV_REFRESH_INBOX"/*

echo "== 3. failure after swap restores the working copy"
export_pair 20990101T020000Z
before="$(echo "SELECT count(*) FROM work.captures;" | psql_dev docs)"
ok "forced post-swap failure refused" '! DOCPLANE_DEV_REFRESH_FAIL_AFTER_SWAP=1 "$D" refresh run >/dev/null 2>&1'
ok "working copy restored (same captures, token works)" '[ "$(echo "SELECT count(*) FROM work.captures;" | psql_dev docs)" = "$before" ] && [ "$(auth "$TOKEN")" = 200 ]'

echo "== 4. successful refresh"
ok "refresh applies" '"$D" refresh run >/dev/null 2>&1'
ok "synthetic copy kept by name" '[ -n "$(echo "SELECT 1 FROM pg_database WHERE datname LIKE '"'"'docs_synthetic_%'"'"';" | psql_dev postgres)" ]'
ok "source capture readable" '[ "$(echo "SELECT count(*) FROM work.captures WHERE body = '"'"'source capture'"'"';" | psql_dev docs)" = 1 ]'
ok "no source token row remains" '[ "$(echo "SELECT count(*) FROM docplane.api_tokens WHERE principal_id = '"'"'00000000-0000-4000-8000-0000000000a1'"'"';" | psql_dev docs)" = 0 ]'
ok "credential-shaped receipt neutralised, plain receipt kept" '[ "$(echo "SELECT count(*) FROM docplane.mutation_receipts WHERE response::text LIKE '"'"'%'"$PROD_TOKEN_SHAPE"'%'"'"';" | psql_dev docs)" = 0 ] && [ "$(echo "SELECT response->>'"'"'ok'"'"' FROM docplane.mutation_receipts WHERE idempotency_key = '"'"'k-plain'"'"';" | psql_dev docs)" = true ]'
ok "source principal kept for attribution" '[ "$(echo "SELECT count(*) FROM work.captures c JOIN docplane.principals p ON p.principal_id = c.author_principal_id WHERE p.display_name = '"'"'source-automation'"'"';" | psql_dev docs)" = 1 ]'
ok "dev principals carried by ID; dev token authenticates" '[ "$(auth "$TOKEN")" = 200 ] && [ "$(echo "SELECT count(*) FROM docplane.principals WHERE principal_id IN (SELECT principal_id::uuid FROM (VALUES $(sed "s/.*/('"'"'&'"'"')/" "$DOCPLANE_DEV_HOME/dev-principals.ids" | paste -sd,)) v(principal_id));" | psql_dev docs)" = "$(wc -l < "$DOCPLANE_DEV_HOME/dev-principals.ids")" ]'
ok "isolation unchanged after refresh" '"$D" check >/dev/null'
ok "fixture refuses a refreshed copy" '! "$D" fixture >/dev/null 2>&1'

echo "== 5. pause, unchanged, lock"
"$D" refresh pause >/dev/null
ok "paused scheduled run does nothing" '"$D" refresh run --scheduled | grep -q paused'
"$D" refresh resume >/dev/null
ok "scheduled run on an already-applied export is a no-op" '"$D" refresh run --scheduled | grep -q unchanged'
exec 8>"$DOCPLANE_DEV_HOME/refresh.lock"; flock 8
ok "concurrent run is refused by the lock" '"$D" refresh run | grep -q "holds the lock"'
exec 8>&-

echo "== 6. identity collision is refused"
docker exec "$SRC_PG" psql -U docs -d docs -q -c "INSERT INTO docplane.principals (principal_id, principal_kind, display_name) VALUES ('$(head -1 "$DOCPLANE_DEV_HOME/dev-principals.ids")', 'HUMAN', 'collision');"
export_pair 20990101T030000Z
ok "source principal with a dev principal ID refused" '! "$D" refresh run >/dev/null 2>&1 && [ "$(auth "$TOKEN")" = 200 ]'

echo; echo "$PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
