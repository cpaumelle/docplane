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
        --mcp-url <consolidated MCP URL> --api-key-file ~/.config/charliehub/mcp-client.env \
        --cafile ~/.config/charliehub/internal-ca.crt [--record-snapshot snap.json]
    python3 -m work_worker decide --ledger ledger.json --identity <key> \
        --status REJECTED --actor <reviewer UUID> --evidence <UUID>...  # local ledger only

The **manifest** is reviewer-supplied: `scope_initiatives` (UUIDs) and
`obligations`, each `{subject_id, obligation_id, kind, label}` plus optional
`evidence_ids`, `subject_activity_ids`, `candidate_cover_ids`. The obligation
ID is assigned once and never derived from wording. The worker does not extract
obligations from text; it consumes the reviewed classification. The label is a
short plain phrase; secret-shaped or long text is refused.

The key is parsed (never sourced) from a 0600 env-style file, never argv, and
the client is stdlib-only (no MCP SDK). Only `docplane_work_get`,
`docplane_work_capture_get`, `docplane_work_captures` and
`docplane_work_initiatives` can be called; any other verb raises before
leaving the process.

## What it checks

* **Declared obligations** (any ledger kind): always proposed as **coverage
  unconfirmed**, never as proof that no work item exists. The worker lists *related
  work* found by existing structured relationships only: an activity's
  `source_capture_id`, an `ACTIVITY` reference, or an initiative's founding capture.
  Activity text is never read. A record's own activities are not related work;
  an ABANDONED related initiative shows its state. A reviewer who finds the
  covering item resolves the proposal citing it; the decision sticks while the
  evidence is unchanged.

  **Contract gap (narrow, documented, no new convention adopted).** Activity
  `references` are bounded typed evidence (`ACTIVITY`, `CONDITION`, `ARTIFACT`,
  `PR`, `COMMIT`; `reference_id` a free string of at most 300 characters, not
  existence-checked). `ARTIFACT` denotes a generated artifact, so a synthetic
  coverage hash would misuse it; an earlier draft of this PR did exactly that
  and was removed. No existing relationship names an *obligation*, so when one
  record carries several obligations the worker cannot tell which one a related
  item covers. Closing that is a WORK schema decision, not a worker one.
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

## Validation (aggregate; run privately, offline and live)

Truth labels come from the earlier classification table, so these measure
coverage and ledger behaviour, **not** extraction accuracy.

**Development samples** (manifest built from the table): CT1150, 15 declared
obligations: 9 of 9 labelled gaps proposed, 0 missed; 4 of 13 labelled proposals
were the known false positives: three recorded under another work item and one
rejected by the pilot reviewer as overtaken. They are kept as regression tests
(`KnownFalsePositives`) and now read "coverage unconfirmed" with a one-step action
naming the candidate, and the overtaken one stays rejected. VM1124, 8 obligations:
4 of 4 labelled gaps, 0 false positives, 4 unlabelled.

**Live read-only acceptance** (attended, consolidated MCP, node's own credential
file): 6 scoped initiatives and 18 subject captures, read twice. Both runs were
complete (0 failed reads, 0 withheld), produced the same ledger, and an offline
replay of the recorded text-free snapshot reproduced it exactly. Requests were
`initialize`, `notifications/initialized`, `tools/call` of
`docplane_work_capture_get` and `docplane_work_get` only; seven write verbs
(including `docplane_work_note` and the triage verbs) were refused client-side
with zero requests sent. Against the preserved copy the same scope differed only
by 7 added activities on one initiative and activity history that the preserved
copy lacked for another; no state, version or capture identity changed.
Live acceptance caught a wrong assumption: the server leaves `activities_truncated`
true on the last page, so completeness is decided by the cursor ending and the
activity count matching (the first live run failed closed and proposed nothing).

**Held-out sample** (12 records never used for the manifest or tuning; 73 initiatives
read live, complete):
* *Obligations discovered independently* (listed by the earlier classifier, unseen
  by the implementation): 6 enumerated, 9 records with none listed, 1 record with
  about ten unenumerated actions. The worker discovers no obligations from text
  by design, so this bucket is not a worker result.
* *Detected from the supplied manifest*: 6 of 6 proposed (3 labelled true gaps,
  2 unlabelled, 1 the classifier marked closed externally; the worker cannot see
  GitHub or the register, so it proposes it as unconfirmed). No related work was
  found for any.
* *Detected without a manifest*: 20 state flags over 73 initiatives and 8
  ownership flags (all automation-owned). Precision is not assessed: it needs a
  reader. Of the 20 state flags, 16 have later activity after the newest
  RESOLUTION, so many may be partial outcomes; each flag now says how many
  activities follow. This is a hypothesis for a separate validation, not a
  tuned rule.
* *Uncertain*: every coverage claim, every external status, and the state flags above.

## Usefulness evaluation (agent-assessed, not human-adjudicated)

* **State flags (20).** Each initiative's full history was read by an agent that
  classified the flag. Result: 0 whole-completion evidence, 0 genuine contradictions,
  18 partial repairs, 2 historical resolutions, 0 uncertain (17 high confidence).
  15 of the 20 initiatives carry several RESOLUTION activities (up to 20): the
  activity type marks sub-outcomes. Precision of this check is 0 of 20, so
  **a RESOLUTION activity must not be reported as a state contradiction**; the
  detector should be removed or demoted before any manual run. One agent judged
  each flag, so this is a strong signal rather than an adjudicated count.
* **Ownership flags (8).** All eight initiatives are owned by the single MCP
  service identity (`charliehub-mcp-automation`); their activities were written
  by 1-9 distinct principals. This is one systemic identity limitation (the MCP
  writes as a shared identity and the model has no accountable-owner field), not
  eight work items. Three of the eight are COMPLETE or ABANDONED and should not
  be flagged at all.
* **Blind extraction (12 records, agent had no manifest or labels).** It listed
  67 candidate obligations (33 OPEN, 30 UNCLEAR, 4 done in the record), about 5.6
  per record, and none came out as obligation-free. The earlier table listed 6
  enumerated obligations, about 10 more unenumerated, and none on 9 records; the
  agent marked 27 as OPEN on the 8 of those records in the sample. Whether that is
  over-extraction or a coarse table is not resolved without a human reading.
* **Existing links.** The agent found 0 structural relationships (it reported 2
  capture ids mentioned in activity text, which this worker deliberately ignores);
  the worker found 0 too. The sample records are untriaged captures, so no links
  exist by construction. Links can only help after triage.

## Known server behaviour: `activities_truncated`

`charliehub-hub2` `mcp/tools/docplane_work.py` (base clone `d8438b35`, line 226) sets
`activities_truncated = len(recent) < len(parsed)`, comparing the window with the
whole history. It is therefore true on every page of a paged walk, including the
last; only the absent `next_activity_offset` marks the end. Minimal synthetic
reproduction (real adapter function, fabricated 5-activity payload, limit 2):

    offset 0 returned 2 of 5 activities_truncated True next_activity_offset 2
    offset 2 returned 2 of 5 activities_truncated True next_activity_offset 4
    offset 4 returned 1 of 5 activities_truncated True next_activity_offset None

The hub2 tests assert only a first page (true) and an unpaged read (false). I found
no capture, issue or PR tracking the final-page case (searched hub2 PRs and issues,
DocPlane search, the preserved capture and initiative inventories); #989 introduced
paging. No capture was filed. This worker already treats the cursor and the count
check as authoritative, so a fix (for example, true only when `next_activity_offset`
is present) is separate and optional.

Not ready for unattended operation: the state check has no demonstrated value,
coverage cannot be confirmed automatically, and the manifest is hand-supplied.

Run all synthetic tests (from the repo root):

    python3 -m unittest discover -s scripts/tests -p 'test_work_worker*.py' -v
