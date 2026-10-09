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
if grep -Eq '^[[:space:]]*CATALOGUE_SOURCE_DSN[[:space:]]*=' "$environment_file"; then
  fail "protected environment must not persist CATALOGUE_SOURCE_DSN"
fi

set -a
# shellcheck disable=SC1090 -- canonical protected path is runtime configuration.
. "$environment_file"
set +a

required_variables=(
  DOCPLANE_API
  CATALOGUE_DB_KEY
  CATALOGUE_DB_DISPLAY
  CATALOGUE_SCHEMAS
  CATALOGUE_SOURCE_DB
  CATALOGUE_SOURCE_USER
  CATALOGUE_SOURCE_PORT
  CATALOGUE_SOURCE_COMPOSE_PROJECT
  CATALOGUE_SOURCE_COMPOSE_SERVICE
)
for variable in "${required_variables[@]}"; do
  [[ -n "${!variable:-}" ]] || fail "required variable $variable is missing"
done
for secret in DOCPLANE_SCHEMA_CATALOGUE_TOKEN CATALOGUE_SOURCE_PASSWORD; do
  file_variable="${secret}_FILE"
  [[ -n "${!file_variable:-}" || -n "${!secret:-}" ]] \
    || fail "required secret source ${secret}_FILE or $secret is missing"
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

network_output="$(
  docker inspect --format '{{range $name, $network := .NetworkSettings.Networks}}{{printf "%s=%s\n" $name $network.IPAddress}}{{end}}' \
    "${containers[0]}"
)" || fail "PostgreSQL endpoint lookup failed"
mapfile -t network_rows < <(printf '%s\n' "$network_output" | sed '/^$/d')
addresses=()
network_names=()
for row in "${network_rows[@]}"; do
  network_names+=("${row%%=*}")
  address="${row#*=}"
  [[ -n "$address" ]] && addresses+=("$address")
done
(( ${#addresses[@]} == 1 )) \
  || fail "PostgreSQL endpoint did not resolve uniquely"
export CATALOGUE_SOURCE_HOST="${addresses[0]}"
python3 - "$CATALOGUE_SOURCE_HOST" <<'PY' || fail "PostgreSQL endpoint validation failed"
import ipaddress
import sys
ipaddress.ip_address(sys.argv[1])
PY
if [[ "${CATALOGUE_SOURCE_SSLMODE:-${PGSSLMODE:-}}" == "disable" ]]; then
  [[ "${CATALOGUE_ENVIRONMENT:-}" == "development" \
    && "${CATALOGUE_SOURCE_IDENTITY:-}" == "VM1124/trevarn" \
    && "${CATALOGUE_SOURCE_DOCKER_NETWORK:-}" == "trevarn-net" \
    && " ${network_names[*]} " == *" trevarn-net "* ]] \
    || fail "sslmode=disable is allowed only for VM1124 Trevarn on trevarn-net"
fi

exec python3 "$repository_root/scripts/schema_catalogue.py" "$@"
