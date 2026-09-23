#!/usr/bin/env bash
set -uo pipefail

# Schedulable entrypoint: intentionally no alternate implementation. The
# same importer, credentials, receipts, idempotency keys and observations are
# used by operators and timers. flock prevents overlapping reconciliation.
#
# The second, read-only invocation publishes observable live-vs-published drift even
# when reconciliation fails — the same shape as run_work_catalogue_reconciliation.sh,
# so both projections report through one metric family and one set of alerts.
repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Keep the lock outside sticky /tmp. Every legitimate caller must share this
# host-visible runtime lock, regardless of whether it is attended or scheduled:
# a unit with PrivateTmp=true gets its own /tmp, so a lock there would exclude
# nothing from an operator running the importer by hand.
lock_file="${DOCPLANE_METER_LIST_LOCK_FILE:-/run/lock/docplane-meter-list.lock}"
metrics_file="${DOCPLANE_METER_LIST_METRICS_FILE:-/var/lib/node_exporter/textfile_collector/docplane_meter_list.prom}"

# A skipped run is the lock doing its job, not a failure. flock exits 1 for "someone
# else holds it", which is indistinguishable from the importer failing, so give the
# conflict its own sentinel: the holder publishes its own result, and a scheduled
# overlap must not raise the reconciliation-failed alert or fail the systemd unit.
readonly FLOCK_CONFLICT_EXIT=75
flock -n -E "$FLOCK_CONFLICT_EXIT" "$lock_file" \
  python3 "$repository_root/scripts/meter_list.py" "$@"
reconcile_status=$?

if (( reconcile_status == FLOCK_CONFLICT_EXIT )); then
  echo "SKIPPED another reconciliation already holds $lock_file" >&2
  reconcile_status=0
fi

success=1
if (( reconcile_status != 0 )); then
  success=0
fi
python3 "$repository_root/scripts/meter_list.py" \
  --metrics-file "$metrics_file" --reconcile-success "$success"
metrics_status=$?

if (( reconcile_status != 0 )); then
  exit "$reconcile_status"
fi
exit "$metrics_status"
