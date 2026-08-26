#!/usr/bin/env bash
set -euo pipefail

# Schedulable entrypoint: intentionally no alternate implementation. The
# same importer, credentials, receipts, idempotency keys and observations are
# used by operators and timers. flock prevents overlapping reconciliation.
repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Keep the lock outside sticky /tmp. Every legitimate caller must share this
# host-visible runtime lock, regardless of whether it is attended or scheduled.
lock_file="${DOCPLANE_METER_LIST_LOCK_FILE:-/run/lock/docplane-meter-list.lock}"

exec flock -n "$lock_file" \
  python3 "$repository_root/scripts/meter_list.py" "$@"
