#!/usr/bin/env bash
# refresh.sh — replace the dev instance's database with a production export.
# Called as `devstack.sh refresh <run|pause|resume|status>`; see README.md.
# Local changes in the dev database are OVERWRITTEN by every refresh.
#
# Input: DOCPLANE_DEV_REFRESH_INBOX receives pairs
#   docplane-<UTCSTAMP>.dump      pg_dump -Fc --no-owner --no-privileges
#   docplane-<UTCSTAMP>.manifest  key=value: dump_sha256, dump_bytes, source_git_sha,
#                                 dumped_at, count_<table>=<rows> (read in the dump's snapshot)
# The manifest is uploaded after the dump; a dump without a matching manifest or
# with a checksum mismatch is an incomplete transfer and is ignored.
#
# Everything below happens in docs_staging, which no dev service connects to:
#   restore -> remove authentication capability (all production api_tokens) ->
#   neutralise verified credential-shaped values in receipts/events (history kept) ->
#   ledger compatibility with DOCPLANE_SRC -> FK validity + row counts equal the
#   source manifest -> carry the dev principals and their tokens by explicit ID ->
#   swap by rename -> health check -> automatic swap-back on any failure.
# The first replacement keeps the synthetic database as docs_synthetic_<date>;
# later ones keep the previous copy as docs_previous.
set -euo pipefail

: "${DEVSTACK:?}" "${DEV_HOME:?}" "${DOCPLANE_SRC:?}" "${API:?}"
INBOX="${DOCPLANE_DEV_REFRESH_INBOX:-}"
STATE="$DEV_HOME/refresh.status.json"
PAUSED="$DEV_HOME/refresh.paused"
LOCK="$DEV_HOME/refresh.lock"
IDS="$DEV_HOME/dev-principals.ids"
DB=docs; STAGING=docs_staging; PREV=docs_previous
TOKEN_RE='dp_[A-Za-z0-9_-]{40,}'   # DocPlane clear-token shape (agent_auth.issue_token)
COUNTED="docplane.principals docs.pages work.captures work.initiatives work.initiative_activities docplane.mutation_receipts docplane.events"

dc() { "$DEVSTACK" _compose "$@"; }
pg() { dc exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$1" -v ON_ERROR_STOP=1 -q -At' _ "$1"; }  # SQL on stdin
pgc() { pg "$1" <<<"$2"; }

record() {  # result message [json fields for a successful apply]
  python3 - "$STATE" "$1" "$2" "${3:-}" <<'PY'
import json, sys, datetime, os
path, result, message, extra = sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4] or "{}")
try:
    state = json.load(open(path))
except Exception:
    state = {}
now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
state.update({"last_attempt_at": now, "last_result": result, "last_message": message})
if result == "ok":
    state["applied"] = {**extra, "applied_at": now}
tmp = path + ".tmp"
json.dump(state, open(tmp, "w"), indent=2); os.replace(tmp, path)
PY
  echo "refresh: $1 — $2"
}

status() {
  if [ -e "$PAUSED" ]; then echo "automatic refresh: PAUSED since $(date -u -r "$PAUSED" +%FT%TZ)"; else echo "automatic refresh: active"; fi
  if [ -e "$STATE" ]; then cat "$STATE"; else echo "no refresh recorded"; fi
  systemctl list-timers docplane-dev-refresh.timer --no-pager 2>/dev/null | sed -n 2p || true
}

latest() {  # "<dump> <manifest>" of the newest complete, checksum-verified pair
  local m d want
  for m in $(ls -1 "$INBOX"/docplane-*.manifest 2>/dev/null | sort -r); do
    d="${m%.manifest}.dump"
    [ -r "$m" ] && [ -r "$d" ] && [ -s "$d" ] || continue   # unreadable must never compare equal
    want="$(field dump_sha256 "$m")"
    [[ "$want" =~ ^[0-9a-f]{64}$ ]] || continue
    [ "$(sha256sum "$d" | cut -d' ' -f1)" = "$want" ] || continue
    echo "$d $m"; return 0
  done
  return 1
}

field() { sed -n "s/^$1=//p" "$2" | head -1; }
fail_staging() { pgc postgres "DROP DATABASE IF EXISTS $STAGING;" >/dev/null || true; record failed "$1; working database untouched"; return 1; }

dev_ids() {  # explicit dev principal IDs; derived once from the synthetic database if absent
  if [ ! -s "$IDS" ]; then
    pgc "$DB" "SELECT principal_id FROM docplane.principals WHERE metadata->>'issued_via' = 'scripts/dev/dev_fixture.py' ORDER BY 1;" > "$IDS"
    chmod 600 "$IDS"
  fi
  python3 - "$IDS" <<'PY'
import sys, uuid
ids = [l.strip() for l in open(sys.argv[1]) if l.strip()]
assert ids and all(str(uuid.UUID(i)) == i for i in ids), "dev-principals.ids must hold canonical UUIDs"
print(",".join("'" + i + "'" for i in ids))
PY
}

swap_back() {  # $1 = name the working copy was kept under
  dc stop docs-api dashboard docs-mcp docs-web >/dev/null 2>&1 || true
  pgc postgres "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB' AND pid <> pg_backend_pid();" >/dev/null
  pgc postgres "DROP DATABASE IF EXISTS docs_failed;"
  pgc postgres "ALTER DATABASE $DB RENAME TO docs_failed;"
  pgc postgres "ALTER DATABASE $1 RENAME TO $DB;"
  dc up -d --no-build --wait docs-api dashboard docs-mcp docs-web >/dev/null
}

prune_inbox() {  # the sender cannot delete: keep only the two newest export stamps, even while paused
  local stamps; stamps="$(ls -1 "$INBOX" 2>/dev/null | sed -n 's/^docplane-\([0-9TZ]*\)\.\(dump\|manifest\)$/\1/p' | sort -u | head -n -2)"
  local s; for s in $stamps; do rm -f "$INBOX/docplane-$s.dump" "$INBOX/docplane-$s.manifest"; done
}

run() {
  local scheduled="${1:-}"
  [ -n "$INBOX" ] && [ -d "$INBOX" ] || { record failed "DOCPLANE_DEV_REFRESH_INBOX unset or missing"; return 1; }
  prune_inbox
  if [ "$scheduled" = --scheduled ] && [ -e "$PAUSED" ]; then echo "refresh: paused; nothing done"; return 0; fi
  local pair dump manifest sha src_sha dumped_at ids
  pair="$(latest)" || { record failed "no complete, checksum-verified export in $INBOX"; return 1; }
  read -r dump manifest <<<"$pair"
  sha="$(field dump_sha256 "$manifest")"; src_sha="$(field source_git_sha "$manifest")"; dumped_at="$(field dumped_at "$manifest")"
  if [ "$scheduled" = --scheduled ] && [ -e "$STATE" ] \
     && [ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("applied",{}).get("dump_sha256",""))' "$STATE")" = "$sha" ]; then
    record unchanged "newest export $(basename "$dump") already applied"; return 0
  fi
  ids="$(dev_ids)"

  # 1. Restore into staging.
  pgc postgres "DROP DATABASE IF EXISTS $STAGING;" >/dev/null
  pgc postgres "CREATE DATABASE $STAGING;" >/dev/null
  dc cp "$dump" postgres:/tmp/refresh.dump >/dev/null
  if ! dc exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d docs_staging --no-owner --no-privileges --exit-on-error /tmp/refresh.dump'; then
    dc exec -T postgres rm -f /tmp/refresh.dump; fail_staging "pg_restore into staging failed"; return 1
  fi
  dc exec -T postgres rm -f /tmp/refresh.dump

  # 2. Ledger compatibility: staging migrations must be a checksum-identical prefix of DOCPLANE_SRC's.
  if ! pgc "$STAGING" "SELECT ordinal || ' ' || filename || ' ' || checksum FROM docplane.schema_migrations ORDER BY ordinal;" \
       | python3 -c '
import hashlib, pathlib, sys
src = {int(p.name[:3]): (p.name, hashlib.sha256(p.read_bytes()).hexdigest())
       for p in pathlib.Path(sys.argv[1]).glob("[0-9][0-9][0-9]_*.sql")}
rows = [l.split() for l in sys.stdin.read().splitlines() if l.strip()]
sys.exit(1 if not rows or any(src.get(int(r[0])) != (r[1], r[2]) for r in rows) else 0)' "$DOCPLANE_SRC/db/migrations"; then
    fail_staging "source ledger is not a checksum-identical prefix of DOCPLANE_SRC migrations"; return 1
  fi

  # 3. Integrity against the source: every FK validated; row counts equal the export snapshot.
  local t got want bad=""
  [ "$(pgc "$STAGING" "SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND NOT convalidated;")" = 0 ] \
    || { fail_staging "unvalidated foreign keys after restore"; return 1; }
  for t in $COUNTED; do
    want="$(field "count_$t" "$manifest")"; got="$(pgc "$STAGING" "SELECT count(*) FROM $t;")"
    [ -n "$want" ] && [ "$got" = "$want" ] || bad="$bad $t($got/${want:-?})"
  done
  [ -z "$bad" ] || { fail_staging "row counts differ from the source snapshot:$bad"; return 1; }

  # 4. Remove authentication capability; neutralise verified credential-shaped values, keep history.
  local scrubbed
  scrubbed="$(pg "$STAGING" <<SQL
BEGIN;
DELETE FROM docplane.api_tokens;
WITH r AS (UPDATE docplane.mutation_receipts SET response = jsonb_build_object('dev_refresh', 'credential-shaped value removed')
           WHERE response::text ~ '$TOKEN_RE' RETURNING 1),
     t AS (UPDATE docs.trust_mutation_receipts SET response = jsonb_build_object('dev_refresh', 'credential-shaped value removed')
           WHERE response::text ~ '$TOKEN_RE' RETURNING 1),
     e AS (UPDATE docplane.events SET metadata = jsonb_build_object('dev_refresh', 'credential-shaped value removed')
           WHERE metadata::text ~ '$TOKEN_RE' RETURNING 1),
     p AS (UPDATE docplane.principals SET metadata = jsonb_build_object('dev_refresh', 'credential-shaped value removed')
           WHERE metadata::text ~ '$TOKEN_RE' RETURNING 1)
SELECT (SELECT count(*) FROM r) || ' ' || (SELECT count(*) FROM t) || ' ' || (SELECT count(*) FROM e) || ' ' || (SELECT count(*) FROM p);
COMMIT;
SQL
)"
  [ "$(pgc "$STAGING" "SELECT (SELECT count(*) FROM docplane.api_tokens)
      + (SELECT count(*) FROM docplane.mutation_receipts WHERE response::text ~ '$TOKEN_RE')
      + (SELECT count(*) FROM docs.trust_mutation_receipts WHERE response::text ~ '$TOKEN_RE')
      + (SELECT count(*) FROM docplane.events WHERE metadata::text ~ '$TOKEN_RE');")" = 0 ] \
    || { fail_staging "authentication material remains in staging"; return 1; }
  local content_shapes
  content_shapes="$(pgc "$STAGING" "SELECT (SELECT count(*) FROM work.captures WHERE body ~ '$TOKEN_RE') + (SELECT count(*) FROM work.initiative_activities WHERE body ~ '$TOKEN_RE');")"

  # 5. Carry the dev principals and their tokens by explicit ID; refuse any identity collision.
  [ "$(pgc "$STAGING" "SELECT count(*) FROM docplane.principals WHERE principal_id IN ($ids);")" = 0 ] \
    || { fail_staging "a dev principal ID already exists in the source"; return 1; }
  local nids; nids="$(tr -cd ',' <<<"$ids" | wc -c)"; nids=$((nids + 1))
  { echo "BEGIN;"
    echo "CREATE TEMP TABLE cp (LIKE docplane.principals); CREATE TEMP TABLE ct (LIKE docplane.api_tokens);"
    echo "COPY cp FROM STDIN;"; pgc "$DB" "COPY (SELECT * FROM docplane.principals WHERE principal_id IN ($ids)) TO STDOUT;"; echo '\.'
    echo "COPY ct FROM STDIN;"; pgc "$DB" "COPY (SELECT * FROM docplane.api_tokens WHERE principal_id IN ($ids) AND revoked_at IS NULL) TO STDOUT;"; echo '\.'
    echo "INSERT INTO docplane.principals SELECT * FROM cp;"
    echo "INSERT INTO docplane.api_tokens SELECT * FROM ct;"
    echo "CREATE TABLE IF NOT EXISTS public.docplane_dev_instance_marker (marker text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now());"
    echo "INSERT INTO public.docplane_dev_instance_marker (marker) VALUES ('DEV-INSTANCE') ON CONFLICT DO NOTHING;"
    echo "CREATE TABLE public.docplane_dev_refresh (source_git_sha text, dumped_at text, dump_sha256 text, refreshed_at timestamptz DEFAULT now());"
    echo "INSERT INTO public.docplane_dev_refresh (source_git_sha, dumped_at, dump_sha256) VALUES ('$src_sha', '$dumped_at', '$sha');"
    echo "COMMIT;"
  } | pg "$STAGING" >/dev/null || { fail_staging "dev principal carry failed"; return 1; }
  [ "$(pgc "$STAGING" "SELECT count(*) FROM docplane.principals WHERE principal_id IN ($ids);")" = "$nids" ] \
    && [ "$(pgc "$STAGING" "SELECT count(DISTINCT principal_id) FROM docplane.api_tokens;")" = "$nids" ] \
    || { fail_staging "dev principals or their tokens are incomplete after carry"; return 1; }
  local pages; pages="$(pgc "$STAGING" "SELECT count(*) FROM docs.pages;")"

  # 6. Swap by rename; the synthetic database is kept once by name, then the previous copy.
  local keep=$PREV
  [ -n "$(pgc postgres "SELECT 1 FROM pg_database WHERE datname LIKE 'docs_synthetic_%';")" ] || keep="docs_synthetic_$(date -u +%Y%m%d)"
  dc stop docs-api dashboard docs-mcp docs-web >/dev/null
  pgc postgres "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('$DB', '$STAGING') AND pid <> pg_backend_pid();" >/dev/null
  [ "$keep" != "$PREV" ] || pgc postgres "DROP DATABASE IF EXISTS $PREV;"
  # ALTER DATABASE cannot run in a transaction, so the two renames are not atomic.
  # If the second fails, compensate by renaming the working copy back before any
  # service starts; the staging copy is left for inspection and dropped next run.
  pgc postgres "ALTER DATABASE $DB RENAME TO $keep;"
  local second="$STAGING"; [ -z "${DOCPLANE_DEV_REFRESH_FAIL_SECOND_RENAME:-}" ] || second="docs_staging_absent_for_test"
  if ! pgc postgres "ALTER DATABASE $second RENAME TO $DB;"; then
    pgc postgres "ALTER DATABASE $keep RENAME TO $DB;"
    dc up -d --no-build --wait docs-api dashboard docs-mcp docs-web >/dev/null
    record failed "second rename failed; working copy renamed back from $keep"; return 1
  fi
  if [ -n "${DOCPLANE_DEV_REFRESH_FAIL_AFTER_SWAP:-}" ] \
     || ! dc up -d --no-build --wait docs-api dashboard docs-mcp docs-web >/dev/null 2>&1 \
     || [ "$(curl -fsS "$API/healthz" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("pages"))')" != "$pages" ]; then
    swap_back "$keep"; record failed "post-swap check failed; working copy restored from $keep"; return 1
  fi
  local old; old="$(ls -1 "$INBOX"/docplane-*.dump "$INBOX"/docplane-*.manifest 2>/dev/null | grep -v -e "$(basename "$dump")" -e "$(basename "$manifest")" || true)"
  [ -z "$old" ] || rm -f $old
  record ok "applied $(basename "$dump"): $pages pages; kept prior database as $keep" \
    "{\"source_git_sha\": \"$src_sha\", \"dumped_at\": \"$dumped_at\", \"dump_sha256\": \"$sha\", \"pages\": $pages, \"kept_as\": \"$keep\", \"neutralised_receipt_trust_event_principal\": \"$scrubbed\", \"token_shapes_in_content\": $content_shapes}"
}

case "${1:-status}" in
  run) shift; exec 9>"$LOCK"; flock -n 9 || { echo "refresh: another refresh holds the lock"; exit 0; }; run "$@";;
  pause) touch "$PAUSED"; echo "automatic refresh paused until 'refresh resume' (manual 'refresh run' still works)";;
  resume) rm -f "$PAUSED"; echo "automatic refresh resumed";;
  status) status;;
  *) echo "usage: devstack.sh refresh run [--scheduled]|pause|resume|status" >&2; exit 2;;
esac
