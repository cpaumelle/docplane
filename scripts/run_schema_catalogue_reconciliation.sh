#!/usr/bin/env bash
set -euo pipefail

# Canonical attended entrypoint for the schema-catalogue reconciler. Stable
# configuration and credentials live in one protected file; the PostgreSQL
# address is runtime state and is deliberately rediscovered for every run.
repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
environment_file="${DOCPLANE_SCHEMA_CATALOGUE_ENV_FILE:-/etc/docplane/schema-catalogue.env}"
lock_file="${DOCPLANE_SCHEMA_CATALOGUE_LOCK_FILE:-/run/lock/docplane-schema-catalogue.lock}"
readonly FLOCK_CONFLICT_EXIT=75

fail() {
  echo "schema-catalogue wrapper: $*" >&2
  exit 78
}

[[ -f "$environment_file" && -r "$environment_file" ]] \
  || fail "protected environment is absent or unreadable"

environment_uid="$(stat -Lc '%u' "$environment_file")" \
  || fail "cannot inspect protected environment"
environment_mode="$(stat -Lc '%a' "$environment_file")" \
  || fail "cannot inspect protected environment"
[[ "$environment_uid" == "$EUID" && "$environment_mode" == "600" ]] \
  || fail "protected environment must be owned by the execution identity with mode 0600"

# A persisted DSN would make a transient container address authoritative.
if grep -Eq '^[[:space:]]*CATALOGUE_SOURCE_DSN=' "$environment_file"; then
  fail "protected environment must not persist CATALOGUE_SOURCE_DSN"
fi

set -a
# shellcheck disable=SC1090 -- canonical protected path is runtime configuration.
. "$environment_file"
set +a

required_variables=(
  DOCPLANE_API
  DOCPLANE_SCHEMA_CATALOGUE_TOKEN
  CATALOGUE_DB_KEY
  CATALOGUE_DB_DISPLAY
  CATALOGUE_SCHEMAS
  CATALOGUE_SOURCE_DB
  CATALOGUE_SOURCE_USER
  CATALOGUE_SOURCE_PASSWORD
  CATALOGUE_SOURCE_PORT
  CATALOGUE_SOURCE_COMPOSE_PROJECT
  CATALOGUE_SOURCE_COMPOSE_SERVICE
)
for variable in "${required_variables[@]}"; do
  [[ -n "${!variable:-}" ]] || fail "required variable $variable is missing"
done

# Hold one host-visible logical exclusion domain across runtime discovery and
# the complete generator process. Expected contention is distinct from an
# invalid lock path or another operational failure.
if ! exec 9>"$lock_file"; then
  fail "cannot open shared lock"
fi
if flock -n -E "$FLOCK_CONFLICT_EXIT" 9; then
  :
else
  status=$?
  if (( status == FLOCK_CONFLICT_EXIT )); then
    echo "SKIPPED another schema-catalogue reconciliation holds the shared lock" >&2
  fi
  exit "$status"
fi

command -v docker >/dev/null 2>&1 || fail "docker is unavailable"
container_output="$(
  docker ps \
    --filter "label=com.docker.compose.project=$CATALOGUE_SOURCE_COMPOSE_PROJECT" \
    --filter "label=com.docker.compose.service=$CATALOGUE_SOURCE_COMPOSE_SERVICE" \
    --format '{{.ID}}'
)" || fail "PostgreSQL runtime lookup failed"
mapfile -t containers < <(printf '%s\n' "$container_output" | sed '/^$/d')
(( ${#containers[@]} == 1 )) \
  || fail "PostgreSQL runtime identity did not resolve uniquely"

address_output="$(
  docker inspect --format '{{range .NetworkSettings.Networks}}{{println .IPAddress}}{{end}}' \
    "${containers[0]}"
)" || fail "PostgreSQL endpoint lookup failed"
mapfile -t addresses < <(printf '%s\n' "$address_output" | sed '/^$/d')
(( ${#addresses[@]} == 1 )) \
  || fail "PostgreSQL endpoint did not resolve uniquely"
export CATALOGUE_SOURCE_HOST="${addresses[0]}"

# Build a libpq URI without putting credentials in argv, persistent files or
# output. Python validates the runtime address and percent-encodes stable
# credential components; only the generator receives the resulting DSN.
CATALOGUE_SOURCE_DSN="$(python3 - <<'PY'
import ipaddress
import os
from urllib.parse import quote

host = os.environ["CATALOGUE_SOURCE_HOST"]
address = ipaddress.ip_address(host)
port = int(os.environ["CATALOGUE_SOURCE_PORT"])
if not 1 <= port <= 65535:
    raise ValueError("invalid PostgreSQL port")
authority_host = f"[{address}]" if address.version == 6 else str(address)
user = quote(os.environ["CATALOGUE_SOURCE_USER"], safe="")
password = quote(os.environ["CATALOGUE_SOURCE_PASSWORD"], safe="")
database = quote(os.environ["CATALOGUE_SOURCE_DB"], safe="")
print(f"postgresql://{user}:{password}@{authority_host}:{port}/{database}", end="")
PY
)" || fail "PostgreSQL endpoint validation failed"
export CATALOGUE_SOURCE_DSN
unset CATALOGUE_SOURCE_HOST

exec python3 "$repository_root/scripts/schema_catalogue.py" "$@"
