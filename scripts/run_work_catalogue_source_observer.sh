#!/usr/bin/env bash
set -uo pipefail

# Source observation is deliberately independent of reconciliation. It uses
# the same exclusion domain, but it never renders, publishes, or interprets
# freshness as authority to generate.
repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
lock_file="${DOCPLANE_WORK_CATALOGUE_LOCK_FILE:-/run/lock/docplane-work-catalogue.lock}"

# Generation and observation share exit 75 for expected nonblocking lock
# contention. A skipped observation records no FAILED evidence: no source
# observation was attempted, and the next timer opportunity will retry.
readonly FLOCK_CONFLICT_EXIT=75
flock -n -E "$FLOCK_CONFLICT_EXIT" "$lock_file" \
  python3 "$repository_root/scripts/work_catalogue.py" \
    --observe-source --status-json
observer_status=$?

if (( observer_status == FLOCK_CONFLICT_EXIT )); then
  echo "SKIPPED work-catalogue exclusion domain is already held" >&2
  exit 0
fi

# Genuine identity, source-read, canonicalisation, and OBSERVE-write failures
# remain visible to systemd exactly as returned by the canonical probe CLI.
exit "$observer_status"
