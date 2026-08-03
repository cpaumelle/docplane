#!/usr/bin/env bash
set -uo pipefail

# One canonical implementation serves operators and the timer. The second,
# read-only invocation publishes observable live-vs-published drift even when
# reconciliation fails; flock prevents overlapping governed changes.
repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Not /tmp: a systemd unit with PrivateTmp=true gets a private /tmp, so a lock
# there excludes nothing from an operator's concurrent manual run — the exact
# overlap this lock exists to prevent. /run/lock is outside the private mount.
lock_file="${DOCPLANE_WORK_CATALOGUE_LOCK_FILE:-/run/lock/docplane-work-catalogue.lock}"
metrics_file="${DOCPLANE_WORK_CATALOGUE_METRICS_FILE:-/var/lib/node_exporter/textfile_collector/docplane_work_catalogue.prom}"

# A skipped run is the lock doing its job, not a failure. flock's default exit
# code for "someone else holds it" is 1, indistinguishable from the generator
# failing, so give the conflict its own sentinel: the holder publishes its own
# result, and a scheduled overlap must not raise the reconciliation-failed
# alert or fail the systemd unit.
readonly FLOCK_CONFLICT_EXIT=75
flock -n -E "$FLOCK_CONFLICT_EXIT" "$lock_file" \
  python3 "$repository_root/scripts/work_catalogue.py" "$@"
reconcile_status=$?

if (( reconcile_status == FLOCK_CONFLICT_EXIT )); then
  echo "SKIPPED another reconciliation already holds $lock_file" >&2
  reconcile_status=0
fi

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
