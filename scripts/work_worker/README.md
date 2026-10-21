# WORK worker: proposal-ledger foundation

This is a development library, not a deployed or scheduled worker. It does
not collect WORK, classify prose, issue credentials, call APIs, write flags
into DocPlane or change underlying work state. The existing WORK catalogue
reconciler maintains generated documentation and remains separate.

The first integration should consume governed MCP safe reads on VM8555 and
reuse Sergio's reviewed classifications. No direct contributor API or SQL
fallback is permitted. Any future collector must distinguish a complete scan
from pagination/read failures, withheld evidence and unsupported projections.

## Ledger contract

`reconcile(previous, candidates, evaluated_subjects=...)` is a pure function.
A candidate has exactly `subject_id`, `kind`, `obligation_id`, and `evidence`.
Evidence consists of canonical `record_id` and `revision_id` UUID pairs.
The obligation ID is assigned once to a distinct discrepancy; it must not be
regenerated from mutable wording, a list position or a detector version.
Three residual obligations on one capture therefore have three identities.

An evaluated subject means all inputs needed by that detector were successfully
read and evaluated, including referenced evidence. A failed/partial/withheld
read cannot establish absence. Callers must exclude that subject. Omission
produces NOT_REEVALUATED or NOT_DETECTED, never automatic resolution.

A rejection or resolution stays effective for identical evidence. Changed
versioned evidence reopens review, preserving prior decisions. A decision on a
flag never changes the lifecycle of its underlying initiative or capture.

The library accepts no prose or raw metadata. The eventual private proposal
report may add a redacted explanation, uncertainty and requested action, but
must validate those at the canonical safe-read boundary before persistence.
The eventual report groups systemic warnings while retaining each affected ID.

`decide` records reviewer identity and evidence references only. This helper is
not an authenticated mutation endpoint or a complete audit log: reason, time,
reviewer authorisation and persistence are integration work, not claimed here.

## Important integration gate

Do not invent revision IDs when the governed read does not expose them.
Immutable activity IDs can identify immutable evidence; mutable records need
an actual version, or an explicitly reviewed digest of their safe projection.
The collector must agree that versioning contract before using this library on
the corpus. The initial UUID-only evidence contract is deliberately narrow.
No secret-shaped free-text exception or raw-body digest is introduced.

## Acceptance on VM8555 (not yet performed)

1. Replay the preserved 29-record pilot, then the VM1124 sample.
2. Verify multiple residuals, correction links and unchanged rejection across
   detector wording changes. Use explicit coverage references, not keywords.
3. Inject a failed page/read and a withheld field: existing flags stay open and
   affected subjects are not reported as fully evaluated.
4. Change the relevant evidence version: review reopens, history stays intact.
5. Review false positives, missed obligations and effort per proposal against
   the preserved classification table. No aggregate accuracy claim from these
   synthetic unit tests.
6. No production writes or scheduling. Review these results before deciding
   on a governed flag store or scheduled agent runtime.

Run local synthetic tests:

    python3 -m unittest discover -s scripts/tests -p test_work_worker_ledger.py -v
