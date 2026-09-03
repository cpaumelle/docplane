#!/usr/bin/env bash
set -uo pipefail

# Source observation shares runtime discovery and the logical exclusion domain
# with generation, but calls only the source-only observer entrypoint.
repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
environment_file="${DOCPLANE_SCHEMA_OBSERVER_ENV_FILE:-/etc/docplane/schema-catalogue-observer.env}"
lock_file="${DOCPLANE_SCHEMA_CATALOGUE_LOCK_FILE:-/run/lock/docplane-schema-catalogue.lock}"
readonly FLOCK_CONFLICT_EXIT=75

fail() {
  echo "schema-catalogue observer wrapper: $*" >&2
  exit 78
}

[[ -f "$environment_file" && -r "$environment_file" ]] \
  || fail "protected environment is absent or unreadable"
environment_uid="$(stat -Lc '%u' "$environment_file")" || fail "cannot inspect protected environment"
environment_mode="$(stat -Lc '%a' "$environment_file")" || fail "cannot inspect protected environment"
[[ "$environment_uid" == "$EUID" && "$environment_mode" == "600" ]] \
  || fail "protected environment must be owned by the execution identity with mode 0600"
grep -Eq '^[[:space:]]*CATALOGUE_SOURCE_DSN=' "$environment_file" \
  && fail "protected environment must not persist CATALOGUE_SOURCE_DSN"

set -a
# shellcheck disable=SC1090 -- canonical protected path is runtime configuration.
. "$environment_file"
set +a

required_variables=(
  DOCPLANE_API DOCPLANE_SCHEMA_OBSERVER_TOKEN CATALOGUE_DB_KEY CATALOGUE_SCHEMAS
  CATALOGUE_SOURCE_DB CATALOGUE_SOURCE_USER CATALOGUE_SOURCE_PASSWORD
  CATALOGUE_SOURCE_PORT CATALOGUE_SOURCE_COMPOSE_PROJECT CATALOGUE_SOURCE_COMPOSE_SERVICE
)
for variable in "${required_variables[@]}"; do
  [[ -n "${!variable:-}" ]] || fail "required variable $variable is missing"
done

if ! exec 9>"$lock_file"; then fail "cannot open shared lock"; fi
if flock -n -E "$FLOCK_CONFLICT_EXIT" 9; then
  :
else
  status=$?
  if (( status == FLOCK_CONFLICT_EXIT )); then
    echo "SKIPPED schema-catalogue exclusion domain is already held" >&2
    exit 0
  fi
  exit "$status"
fi

command -v docker >/dev/null 2>&1 || fail "docker is unavailable"
container_output="$(docker ps \
  --filter "label=com.docker.compose.project=$CATALOGUE_SOURCE_COMPOSE_PROJECT" \
  --filter "label=com.docker.compose.service=$CATALOGUE_SOURCE_COMPOSE_SERVICE" \
  --format '{{.ID}}')" || fail "PostgreSQL runtime lookup failed"
mapfile -t containers < <(printf '%s\n' "$container_output" | sed '/^$/d')
(( ${#containers[@]} == 1 )) || fail "PostgreSQL runtime identity did not resolve uniquely"

address_output="$(docker inspect --format '{{range .NetworkSettings.Networks}}{{println .IPAddress}}{{end}}' \
  "${containers[0]}")" || fail "PostgreSQL endpoint lookup failed"
mapfile -t addresses < <(printf '%s\n' "$address_output" | sed '/^$/d')
(( ${#addresses[@]} == 1 )) || fail "PostgreSQL endpoint did not resolve uniquely"
export CATALOGUE_SOURCE_HOST="${addresses[0]}"

CATALOGUE_SOURCE_DSN="$(python3 - <<'PY'
import ipaddress
import os
from urllib.parse import quote

address = ipaddress.ip_address(os.environ["CATALOGUE_SOURCE_HOST"])
port = int(os.environ["CATALOGUE_SOURCE_PORT"])
if not 1 <= port <= 65535:
    raise ValueError("invalid PostgreSQL port")
host = f"[{address}]" if address.version == 6 else str(address)
user = quote(os.environ["CATALOGUE_SOURCE_USER"], safe="")
password = quote(os.environ["CATALOGUE_SOURCE_PASSWORD"], safe="")
database = quote(os.environ["CATALOGUE_SOURCE_DB"], safe="")
print(f"postgresql://{user}:{password}@{host}:{port}/{database}", end="")
PY
)" || fail "PostgreSQL endpoint validation failed"
export CATALOGUE_SOURCE_DSN
unset CATALOGUE_SOURCE_HOST

python3 "$repository_root/scripts/schema_catalogue_observer.py" "$@"
observer_status=$?
unset CATALOGUE_SOURCE_DSN
exit "$observer_status"
