#!/usr/bin/env bash
set -uo pipefail

# One canonical implementation serves operators and the timer. The second,
# read-only invocation publishes observable live-vs-published drift even when
# reconciliation fails; flock prevents overlapping governed changes.
repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
lock_file="${DOCPLANE_WORK_CATALOGUE_LOCK_FILE:-/tmp/docplane-work-catalogue.lock}"
metrics_file="${DOCPLANE_WORK_CATALOGUE_METRICS_FILE:-/var/lib/node_exporter/textfile_collector/docplane_work_catalogue.prom}"

flock -n "$lock_file" python3 "$repository_root/scripts/work_catalogue.py" "$@"
reconcile_status=$?

success=1
if (( reconcile_status != 0 )); then
  success=0
fi
python3 "$repository_root/scripts/work_catalogue.py" \
  --metrics-file "$metrics_file" --reconcile-success "$success"
metrics_status=$?

if (( reconcile_status != 0 )); then
  exit "$reconcile_status"
fi
exit "$metrics_status"
