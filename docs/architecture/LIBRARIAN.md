# The Librarian

**Status: designed, partly built as a read-only development tool, not finished.**
Nothing described here runs on a schedule, holds a production credential or
writes to WORK.

## Purpose

WORK fills up with captures, notes and reports from many agents. Each record is
useful on its own, but scattered: the same obligation is stated three times, a
correction sits on a different initiative from the claim it corrects, and an
open follow-up hides inside a long report. Counting INBOX rows does not tell the
next executor what is being achieved or what is left.

The **Librarian** is the planned answer. It reads WORK, works out how each new
record relates to existing work, and **proposes** how to organise it, so that the
next human or agent sees one coherent picture of each deliverable with evidence
behind every line. DocPlane WORK stays the master backlog; the Librarian guards
it and never becomes a second list.

Two parts divide the labour:

- a **reasoning agent** reads safe (redacted) record content and makes bounded
  interpretations: what each part of a record is, which deliverable owns it, and
  what is still open;
- **deterministic code** handles everything that must be exact: collection
  integrity, identities, checkpoints, repeat runs, validation of the agent's
  output, and the record of reviewer decisions.

## How it fits WORK

- **Briefs.** A [WORK brief](WORK_BRIEF.md) is the human-and-agent-maintained
  view of one initiative's named items. The Librarian's output is a *derived*
  briefing it can rebuild, plus proposals. It does not write briefs.
- **Safe reads.** It reads only through the redacted, allowlisted reads in
  [Safe WORK reads](WORK_CAPTURE_READS.md).
- **Proposal ledger.** Every discrepancy it finds becomes a proposal in a local,
  private ledger with a stable identity, so a reviewer's decision sticks across
  runs while the evidence is unchanged.
- **Reconciliation.** It proposes filing, linking, consolidation, verification
  and next actions. A human decides; the existing triage and note verbs carry out
  any accepted change. The Librarian names those verbs; it never calls them.

## What is designed

The design is in [`design/work-librarian/README.md`](../../design/work-librarian/README.md),
with 21 synthetic evaluation cases in
[`scenarios.json`](../../design/work-librarian/scenarios.json). It is a proposal
under review, not policy. In summary:

- **Six responsibilities:** classify each part of a record (obligation, evidence,
  decision, correction, handover, background or suggestion); propose the owning
  deliverable; connect evidence, corrections and dependencies; reconcile new
  information with prior understanding without letting a later timestamp alone
  win; propose filing, links or new trackable obligations; and assemble a
  next-agent briefing.
- **One bounded run:** an explicit, frozen scope; a read budget; claims tested
  against counter-evidence; a delta against the previous briefing ("no change" is
  a valid result); and output validated before anything is stored.
- **Intake for everything incoming:** every new capture and activity gets exactly
  one evaluation, such as contributes evidence, adds an obligation, warrants a
  new deliverable, duplicates, corrects, supersedes or uncertain. A record's own
  lifecycle (INBOX, attached, discarded) never stands in for whether it was
  evaluated.
- **Honesty rules:** unknown stays unknown; a WORK read confirms what was
  recorded, never that an external action (merge, deploy) happened; a briefing is
  marked `INCOMPLETE` whenever anything was not read; absence after a partial
  scan resolves nothing.
- **Authority progression:** first read and propose privately; later, perhaps,
  reversible filing in a bounded delegated scope once tools exist and accuracy has
  been reviewed; creating items, assigning work, lifecycle changes and completion
  always need separate approval.

## What is built

[`scripts/work_worker/`](../../scripts/work_worker/README.md) is a manually
invoked, development-stage worker. It:

- reads WORK only through four read verbs (`docplane_work_get`,
  `docplane_work_capture_get`, `docplane_work_captures`,
  `docplane_work_initiatives`) or a recorded snapshot. Any other verb is refused
  before a request leaves the process. KNOW read verbs are allowed only when the
  run configuration says KNOW access is available;
- keeps a **proposal ledger** (`ledger.py`): a pure `reconcile` function with
  stable obligation identities and versioned evidence. A rejection or resolution
  stays in force for identical evidence, and changed evidence reopens review. A
  failed or partial read never resolves anything;
- **validates an interpreting agent's output** (`interpretation.py`,
  `interpretation.schema.json`, `evaluations.py`) against the design's rules,
  including read-log-derived search scope, coverage totals and continuity between
  runs;
- supports a **repeatable manual run** (`RUN-PROCEDURE.md`): pin a configuration,
  read through a logging read tool, check, mint IDs once, and render a briefing of
  at most 400 words plus a full appendix and ledger into a new directory.

Its only write is a local ledger file with mode 0600. It does not call the
contributor API, issue credentials, schedule anything, or write to WORK or
KNOW. The interpreting agent is launched by hand.

The read verbs it calls are those of a deployment's consolidated MCP adapter.
The bundled `mcp/` server in this repository does not provide them.

Tests: `python3 -m unittest discover -s scripts/tests -p 'test_work_worker*.py'`.
All fixtures are synthetic.

## What is not built

- Scheduling or any unattended runtime.
- An accepted agent runtime, credential or data-processing boundary for the
  interpreting agent.
- Any write path: no filing, linking, metadata change, capture, note or triage.
- A way to retire a proposal explicitly.
- A WORK relationship that names an individual obligation. When one record
  carries several obligations, the worker cannot tell which one a related item
  covers. The worker README records this as a WORK schema decision, not a worker
  one.
- Measured extraction recall, and evaluation results beyond regression evidence.
- A pilot on live WORK beyond manually reviewed, read-only runs.

## Current status

- Manually reviewed read-only runs, a filing rehearsal on a disposable copy and
  an independent scoring of a held-out run have been done. Each exposed defects
  that are now validator rules with synthetic tests. Those reruns are
  **regression evidence, not independent validation**.
- The worker's state check (a `RESOLUTION` activity on a non-terminal
  initiative) had no demonstrated value: an agent review judged 0 of 20 flags to
  be real contradictions. Its README says the detector should be removed or
  demoted before any further manual run.
- The worker README says it is **not ready for unattended operation**: the state
  check has no demonstrated value, coverage cannot be confirmed automatically, and
  the manifest is hand-supplied.
- WORK records that the owner chose the deterministic brief and update slice
  instead of the Librarian's proposed first improvement increment, and that
  further classification batches and bulk historical filing are on hold until
  the owner decides otherwise.

## Open decisions in the design

- Which agent runtime, credentials, scheduling and data-processing boundaries are
  acceptable before any unattended operation.
- The practical accuracy and review-effort threshold, to be agreed from held-out
  results before any scheduling.
- Whether and when to move to reversible filing in a delegated scope, and which
  governed tools that needs.
- Where private evidence and interpretations may be stored, and for how long.
- How to close the known recall gap: when the only record stating an action is
  one the Librarian itself wrote, the current rules leave nothing to extract.
- The briefing's trade of breadth for precision: a successor can miss older
  related items.

## What "finished" requires

These come from the design's own acceptance section; no other roadmap exists.

1. All synthetic scenarios in `scenarios.json` pass.
2. A held-out sample of private real records is interpreted without the old
   manifest or labels, and reviewed independently: two reviewers extract on their
   own first, an arbiter settles disputed items from source evidence, and a
   random control sample of records with no proposals is mixed in to measure
   misses.
3. Results are reported separately for obligation extraction precision and
   recall, placement, relationship correctness, unsupported state or ownership
   claims, missed residuals, review effort, retrieval cost, repeat-run stability
   and briefing usefulness, with counts and disagreements rather than one score.
4. Before a manually reviewed live run: every proposed claim is traceable, known
   partial-outcome cases avoid false closure, no unsafe action occurs, and the
   held-out review shows useful organisation at acceptable review effort.
5. Before scheduling: the reviewer and the owner agree the practical threshold
   from those results, and the runtime, credentials, scheduling and
   data-processing boundaries are accepted.
6. Any write authority beyond private proposals arrives only after the governed
   tools exist and accuracy has been reviewed, one bounded, reversible scope at a
   time.
