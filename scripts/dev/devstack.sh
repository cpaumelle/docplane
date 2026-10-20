#!/usr/bin/env bash
# devstack.sh — persistent, isolated DocPlane development instance holding either
# synthetic data or a refreshed copy of a production export. See scripts/dev/README.md.
#
#   devstack.sh init                 dev-only secrets + DEV branding (once; keeps existing)
#   devstack.sh check                assert the merged Compose model is isolated
#   devstack.sh start                build DOCPLANE_SRC at its HEAD and start (volumes kept)
#   devstack.sh provision            dev-only principals via the bootstrap API (tokens 0600)
#   devstack.sh fixture              load the synthetic WORK fixture (idempotent)
#   devstack.sh status               provenance, health, bindings, fixture counts
#   devstack.sh stop                 stop containers; volumes kept
#   devstack.sh reset                drop THIS project's volumes, then start+provision+fixture
#                                    (requires DOCPLANE_DEV_RESET_CONFIRM=<project>)
#   devstack.sh compat <ref>|stop    extra API container built from <ref> (e.g. origin/main) on the
#                                    project network as `compat-api`, no published port; for
#                                    older-server consumer tests. `stop` removes it.
#   devstack.sh test                 full pytest suite against a DISPOSABLE PostgreSQL
#                                    (DOCPLANE_TEST_ARGS replaces the default test roots)
#   devstack.sh refresh run|pause|resume|status
#                                    replace the database with the newest export in
#                                    DOCPLANE_DEV_REFRESH_INBOX (refresh.sh); local changes are overwritten
#
# Environment:
#   DOCPLANE_SRC          checkout to build and run (default: this repository)
#   DOCPLANE_DEV_PROJECT  Compose project (default docplane-dev)
#   DOCPLANE_DEV_HOME     secrets/state directory (default ~/.config/docplane-dev)
#
# Refuses hosts running a real `docplane` project. Never prints a secret. Secrets live in $DOCPLANE_DEV_HOME (0700/0600) and are
# generated locally; nothing is copied from any other installation.
set -euo pipefail

TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export DOCPLANE_SRC="$(cd "${DOCPLANE_SRC:-$TOOLS/../..}" && pwd)"
export DOCPLANE_DEV_PROJECT="${DOCPLANE_DEV_PROJECT:-docplane-dev}"
DEV_HOME="${DOCPLANE_DEV_HOME:-$HOME/.config/docplane-dev}"
ENV_FILE="$DEV_HOME/dev.env"
export DOCPLANE_DEV_BRANDING="$DEV_HOME/branding"
API="http://127.0.0.1:${DOCPLANE_DEV_API_PORT:-28010}"
FRONT="http://127.0.0.1:${DOCPLANE_DEV_SITE_PORT:-28080}"

die() { echo "devstack: $*" >&2; exit 1; }
log() { printf '== %s\n' "$*"; }

guard_host() {
  # Never share a host with a real installation, and honour a deployment-local
  # refusal pattern for named production hosts (e.g. DOCPLANE_DEV_REFUSE_HOSTS='prod*').
  if [ -n "${DOCPLANE_DEV_REFUSE_HOSTS:-}" ]; then
    # shellcheck disable=SC2254
    case "$(hostname)" in $DOCPLANE_DEV_REFUSE_HOSTS) die "refusing to run on $(hostname) (DOCPLANE_DEV_REFUSE_HOSTS)";; esac
  fi
  if [ -n "$(docker ps -q --filter label=com.docker.compose.project=docplane)" ]; then
    die "a Compose project named 'docplane' is running on this host; refusing to share it with a dev instance"
  fi
  case "$DOCPLANE_DEV_PROJECT" in docplane|docplane_*|"") die "project name '$DOCPLANE_DEV_PROJECT' could collide with a real installation";; esac
}

compose() {
  docker compose -p "$DOCPLANE_DEV_PROJECT" --project-directory "$DOCPLANE_SRC" \
    --env-file "$ENV_FILE" -f "$DOCPLANE_SRC/docker-compose.yml" -f "$TOOLS/compose.dev.yml" "$@"
}

rand() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

cmd_init() {
  guard_host
  umask 077
  mkdir -p "$DEV_HOME/tokens" "$DOCPLANE_DEV_BRANDING"
  chmod 700 "$DEV_HOME" "$DEV_HOME/tokens"
  if [ -e "$ENV_FILE" ]; then
    log "keeping existing $ENV_FILE"
  else
    cat > "$ENV_FILE" <<EOF
POSTGRES_PASSWORD=$(rand)
DOCPLANE_EVENT_CURSOR_SECRET=$(rand)
MCP_API_KEY=$(rand)
DOCPLANE_BOOTSTRAP_TOKEN=$(rand)
DOCPLANE_TOKEN=
DOCPLANE_ACCESS_PROFILE=managed
DOCPLANE_PUBLICATION_WEBHOOKS=
DOCPLANE_SITE_NAME=DocPlane DEV — synthetic data
DOCPLANE_API_PORT=127.0.0.1:${DOCPLANE_DEV_API_PORT:-28010}
DOCPLANE_DASHBOARD_PORT=127.0.0.1:${DOCPLANE_DEV_DASHBOARD_PORT:-28051}
DOCPLANE_SITE_PORT=127.0.0.1:${DOCPLANE_DEV_SITE_PORT:-28080}
DOCPLANE_MCP_PORT=${DOCPLANE_DEV_MCP_PORT:-28049}
EOF
    chmod 600 "$ENV_FILE"
    log "generated dev-only secrets in $ENV_FILE (values not shown)"
  fi
  umask 022
  cat > "$DOCPLANE_DEV_BRANDING/brand.css" <<'CSS'
/* DocPlane DEV instance: synthetic data only. */
body::before { content: "DocPlane DEV \2014 synthetic data"; display: block; position: sticky; top: 0;
  z-index: 10000; padding: 4px 12px; background: #b00020; color: #fff; font: 600 13px/1.6 sans-serif; text-align: center; }
CSS
  chmod 755 "$DOCPLANE_DEV_BRANDING"; chmod 644 "$DOCPLANE_DEV_BRANDING/brand.css"
}

cmd_check() {
  guard_host
  [ -r "$ENV_FILE" ] || die "run init first"
  local cfg; cfg="$(compose config --format json)"
  python3 "$TOOLS/check_isolation.py" "$DOCPLANE_DEV_PROJECT" <<<"$cfg"
}

source_sha() {
  git -C "$DOCPLANE_SRC" diff --quiet HEAD -- || die "DOCPLANE_SRC has uncommitted changes; build only committed source"
  git -C "$DOCPLANE_SRC" rev-parse HEAD
}

marker() {
  # The dev marker is created in THIS project's database only (by start, and by
  # refresh in staging); direct-SQL tooling refuses any database without it.
  compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -q' <<'SQL'
CREATE TABLE IF NOT EXISTS public.docplane_dev_instance_marker (
  marker text PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now());
INSERT INTO public.docplane_dev_instance_marker (marker)
  SELECT 'DEV-INSTANCE' WHERE NOT EXISTS (SELECT 1 FROM public.docplane_dev_instance_marker);
SQL
}

cmd_start() {
  guard_host; cmd_check
  local sha; sha="$(source_sha)"
  export GIT_SHA="$sha" BUILD_TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  log "building $DOCPLANE_SRC at $sha"
  compose build --quiet
  compose up -d --wait
  local got; got="$(curl -fsS "$API/healthz" | python3 -c 'import json,sys; print((json.load(sys.stdin).get("build") or {}).get("git_sha"))')"
  [ "$got" = "$sha" ] || die "healthz git_sha $got != source $sha"
  marker
  printf 'source=%s\nsha=%s\nbuilt=%s\n' "$DOCPLANE_SRC" "$sha" "$BUILD_TIMESTAMP" > "$DEV_HOME/state"
  log "running $sha; API $API; front $FRONT"
}

cmd_provision() { guard_host; python3 "$TOOLS/dev_fixture.py" provision --api "$API" --env-file "$ENV_FILE" --tokens "$DEV_HOME/tokens"; }

cmd_fixture() {
  guard_host
  python3 "$TOOLS/dev_fixture.py" load --api "$API" --tokens "$DEV_HOME/tokens" \
    --sql-runner "$TOOLS/devstack.sh" "$@"
}

# Internal: feed SQL on stdin to this project's database (used by dev_fixture.py).
cmd_psql() {
  guard_host
  local has_marker
  has_marker="$(compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) > 0 FROM public.docplane_dev_instance_marker"' </dev/null 2>/dev/null || true)"
  [ "$has_marker" = "t" ] || die "database lacks the dev-instance marker; refusing direct SQL"
  compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -q -At'
}

cmd_status() {
  guard_host
  [ -r "$DEV_HOME/state" ] && cat "$DEV_HOME/state"
  compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}\t{{.Ports}}'
  curl -fsS "$API/healthz" | python3 -c 'import json,sys; d=json.load(sys.stdin); print({"status": d.get("status"), "migrations": d.get("migrations"), "git_sha": (d.get("build") or {}).get("git_sha")})' || true
  printf 'SELECT status, count(*) FROM work.captures GROUP BY 1 ORDER BY 1;\n' | cmd_psql || true
  cmd_refresh status || true
}

cmd_stop() { guard_host; compose stop; }

cmd_reset() {
  guard_host
  [ "${DOCPLANE_DEV_RESET_CONFIRM:-}" = "$DOCPLANE_DEV_PROJECT" ] || die "set DOCPLANE_DEV_RESET_CONFIRM=$DOCPLANE_DEV_PROJECT to drop this project's volumes"
  log "volumes to remove (project $DOCPLANE_DEV_PROJECT only):"
  docker volume ls -q --filter "label=com.docker.compose.project=$DOCPLANE_DEV_PROJECT"
  compose down -v --remove-orphans
  rm -f "$DEV_HOME"/tokens/*.token "$DEV_HOME/state" "$DEV_HOME/dev-principals.ids"
  cmd_start; cmd_provision; cmd_fixture
}

# Full repository test suite, as CI runs it, against a throwaway PostgreSQL in its
# own project. The persistent instance is never a test target.
cmd_test() {
  guard_host
  local tp="${DOCPLANE_DEV_PROJECT}-test-$$" net pg rc=0
  net="${tp}_net"; pg="${tp}_pg"
  cleanup_test() { docker rm -f "$pg" >/dev/null 2>&1 || true; docker network rm "$net" >/dev/null 2>&1 || true; }
  trap cleanup_test RETURN
  docker network create --label "org.docplane.dev-test=$tp" "$net" >/dev/null
  docker run -d --name "$pg" --network "$net" --label "org.docplane.dev-test=$tp" \
    -e POSTGRES_DB=docs -e POSTGRES_USER=docs -e POSTGRES_PASSWORD=docs-test postgres:16-alpine >/dev/null
  until docker exec "$pg" pg_isready -U docs -d docs >/dev/null 2>&1; do sleep 1; done
  local sha; sha="$(git -C "$DOCPLANE_SRC" rev-parse HEAD)"
  log "test suite at $sha (disposable PostgreSQL $pg)"
  docker run --rm --network "$net" -v "$DOCPLANE_SRC:/src:ro" -v docplane-dev-test-pip:/root/.cache/pip \
    -e DB_HOST="$pg" -e DB_PORT=5432 -e DB_NAME=docs -e DB_USER=docs -e DB_PASS=docs-test \
    -e DOCPLANE_BOOTSTRAP_TOKEN=test-bootstrap-token -e DOCPLANE_EVENT_CURSOR_SECRET=test-event-cursor-secret-123456789 \
    -e PYTHONPATH=docs-api:dashboard -e PYTHONDONTWRITEBYTECODE=1 -e PYTEST_ADDOPTS="-p no:cacheprovider" \
    python:3.11-slim sh -c '
      set -e; cp -a /src /w; cd /w
      pip install -q --root-user-action=ignore -r docs-api/requirements.txt pytest==8.3.5 httpx==0.27.2
      python docs-api/migrate.py apply --dir db/migrations >/dev/null
      python docs-api/migrate.py status --dir db/migrations | tail -3
      python -m pytest -q '"${DOCPLANE_TEST_ARGS:-docs-api/tests mcp/tests dashboard/tests migration/tests scripts/tests}"'
    ' || rc=$?
  return $rc
}

# An older (or any) API build serving the same dev database, reachable only on the
# project network as compat-api:8010. Migrations are container-owned; this is only
# safe when <ref>'s migration set is a prefix of the running one, which is checked.
cmd_compat() {
  guard_host
  local ref="${1:?usage: devstack.sh compat <git-ref>|stop}" file="$DEV_HOME/compat.yml"
  if [ "$ref" = stop ]; then
    [ -e "$file" ] && compose -f "$file" rm -sf compat-api; rm -f "$file"; return 0
  fi
  local sha tree; sha="$(git -C "$DOCPLANE_SRC" rev-parse --verify "$ref^{commit}")"
  tree="$(mktemp -d)"; git -C "$DOCPLANE_SRC" archive "$sha" | tar -x -C "$tree"
  if diff <(ls "$tree/db/migrations") <(ls "$DOCPLANE_SRC/db/migrations") | grep -q '^<'; then
    rm -rf "$tree"; die "$ref has migrations the running instance lacks"
  fi
  docker build -q -f "$tree/docs-api/Dockerfile" --build-arg GIT_SHA="$sha" --build-arg BUILD_TIMESTAMP="$(date -u +%FT%TZ)" \
    -t "${DOCPLANE_DEV_PROJECT}-compat-api:${sha:0:12}" "$tree" >/dev/null
  rm -rf "$tree"
  printf '%s\n' \
    'services:' \
    '  compat-api:' \
    "    extends: {file: $DOCPLANE_SRC/docker-compose.yml, service: docs-api}" \
    '    environment: {DOCPLANE_PUBLICATION_WEBHOOKS: "", DOCPLANE_ACCESS_PROFILE: managed}' \
    "    image: ${DOCPLANE_DEV_PROJECT}-compat-api:${sha:0:12}" \
    '    build: !reset null' \
    '    ports: !reset []' \
    '    labels:' \
    '      org.docplane.dev-instance: "synthetic-data-only"' \
    "      org.docplane.dev-compat-ref: \"$sha\"" > "$file"
  compose -f "$file" up -d --no-deps --wait compat-api
  log "compat-api running $sha (project network only)"
}

cmd_refresh() { guard_host; DEVSTACK="$TOOLS/devstack.sh" DEV_HOME="$DEV_HOME" API="$API" bash "$TOOLS/refresh.sh" "$@"; }

# Internal: this project's Compose command (used by refresh.sh).
cmd__compose() { compose "$@"; }

case "${1:-}" in
  init|check|start|provision|fixture|status|stop|reset|test|psql|compat|refresh|_compose) c="$1"; shift; "cmd_$c" "$@";;
  *) sed -n '2,23p' "$0"; exit 2;;
esac
