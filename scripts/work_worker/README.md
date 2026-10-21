# WORK worker: read-only reconciliation proposals

This is a manually invoked, development-stage worker, not a deployed or
scheduled one. It reads WORK through the consolidated MCP's read verbs
(or a snapshot), applies the ledger below and prints a private proposal. It
does not classify prose, issue credentials, call the contributor API, write to
WORK or DocPlane, or change any record. Its only write is a local 0600 ledger file. The existing WORK catalogue
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

## Running it (from `scripts/`)

    python3 -m work_worker run --manifest m.json --ledger ledger.json --snapshot snap.json
    python3 -m work_worker run --manifest m.json --ledger ledger.json \
        --mcp-url <consolidated MCP URL> --bearer-file <0600 file>      # needs the `mcp` SDK
    python3 -m work_worker decide --ledger ledger.json --identity <key> \
        --status REJECTED --actor <reviewer UUID> --evidence <UUID>...  # local ledger only

The **manifest** is reviewer-supplied: `scope_initiatives` (UUIDs) and
`obligations`, each `{subject_id, obligation_id, kind, label}` plus optional
`evidence_ids`, `subject_activity_ids`, `candidate_cover_ids`. The obligation
ID is assigned once and never derived from wording. The worker does not extract
obligations from text; it consumes the reviewed classification. The label is a
short plain phrase; secret-shaped or long text is refused.

The bearer is read from a file, never argv. Only `docplane_work_get`,
`docplane_work_capture_get`, `docplane_work_captures` and
`docplane_work_initiatives` can be called; any other verb raises before
leaving the process.

## What it checks

* **Declared obligations** (any ledger kind): covered only when one activity in
  a scoped, fully read initiative carries both (a) a link to the source record
  (`source_capture_id`, an `ACTIVITY` reference, or the initiative's founding
  capture) and (b) an `ARTIFACT` reference equal to
  `sha256("work-worker-obligation:v1:<subject>:<obligation>")`. Activity text is
  never read, so a record repeating an item, or a stray "06:30", cannot cover
  it. The token convention is **proposed, not yet agreed** with WORK producers;
  until it is, expect residuals recorded by hand to be proposed once with a
  one-step "add this reference" action. A covering initiative that is
  `ABANDONED` does not count; a record's own activities never cover it.
* **`STATE_CONTRADICTS_EVIDENCE`**: a `RESOLUTION` activity on a non-terminal initiative.
* **`ACCOUNTABILITY_UNCLEAR`**: owner missing or `AUTOMATION` (records who promoted, not who is accountable).
  Three or more open items of this systemic kind print as one scope-level
  decision that lists every affected record; the ledger still holds each identity.

## Evidence versioning (decision for the #215 integration gate)

Checked on the live consolidated MCP: `docplane_work_get` returns an integer
initiative `version`, `owner`, per-activity `references` and `source_capture_id`,
`founding_capture_ids`, `next_activity_offset`. Captures and individual
activities expose **no revision**. Revisions are not invented. Evidence
versions are `proj-v1:<sha256>` digests of a small allowlisted scalar
projection chosen per detector (captures and activities: immutable identity
fields; state check: `work_state` plus RESOLUTION ids; ownership: principal and
kind). Bodies are never digested, so an unrelated initiative edit does not
reopen a rejection and a material change does. A UUID is no longer accepted as a version.

Known limits: an edited capture body would not change its version (captures
have no edit verb in the governed surface); `docplane_work_initiatives` has a
`truncated` flag but no cursor, so scope is an explicit initiative list.

## Failure and repeat-run rules

Failed, truncated (no usable cursor), count-mismatched, withheld, or
reference-withheld reads exclude the affected subject: its flags become
`NOT_REEVALUATED`, nothing opens or closes, and the report says so. An initiative
read that omits `owner` is excluded rather than treated as ownerless. Uncovered
claims need a complete scope, so one failed scope read blocks them all.
Absence after a full read is `NOT_DETECTED`: the flag stays OPEN for a reviewer.
Record text never reaches the output and nothing in it is acted on.

## Validation on the preserved samples (aggregate; run offline, private)

Replayed CT1150 (29 captures, 11 pilot initiatives) and VM1124 (8 declared
obligations) from the preserved inventory. Truth labels are from the earlier
classification table, so this measures coverage and ledger behaviour, **not**
extraction accuracy (the manifest comes from that same table).

| | CT1150 | VM1124 |
|---|---|---|
| Declared obligations | 15 | 8 |
| Labelled true gaps proposed / missed | 9 / 0 | 4 / 0 |
| False positives (labelled) | 4 of 13 | 0 of 4 |
| Unlabelled proposals | 2 | 4 |
| Review decisions (11 ownership records grouped) | 17 | 8 |

The four CT1150 false positives: three were recorded under another work item
without an explicit reference (each now a one-step "add this reference" action
naming the candidate, not a new task); one was a proposal the pilot reviewer
had rejected as overtaken. VM1124 had no scoped initiatives, so nothing there
could be found covered. Obligation extraction recall and the two multi-action
records the table does not enumerate were not tested. Effort proxy: about 85
words and 1-3 records to open per decision. The pilot's two state flags became
three because this run includes the pilot umbrella the pilot skipped.

## Acceptance still pending

Live acceptance against the consolidated MCP is not done: the `mcp` client was
not exercised (tests inject the call function) and no live manifest exists.
Run it once the MCP release lands, read-only, and compare with a snapshot replay.

Run all synthetic tests (from the repo root):

    python3 -m unittest discover -s scripts/tests -p 'test_work_worker*.py' -v
