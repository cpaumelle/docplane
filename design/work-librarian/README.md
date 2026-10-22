Warning: truncated output (original token count: 7427)
Total output lines: 330

# DocPlane Librarian — interpretation contract (proposed)

Status: design for review, not agent policy or a deployed service.
No new schema, relationship type, identity or mutation authority is established.
Complements worker PRs #215/#216; does not replace their transport or ledger.

The proposed human-facing work briefing, backlog, proposal ledger and run views
are specified in [Dashboard product and integration contract](DASHBOARD.md).

## Outcome and division of labour

The librarian makes scattered WORK strengthen an existing deliverable rather
than merely reducing INBOX counts. The next executor should see what is being
achieved, what changed, what remains, and which evidence supports that view.

A reasoning agent reads safe record content and makes bounded interpretations.
Deterministic code handles collection integrity, identity, checkpoints, repeat
runs and review decisions. A generated manifest is an intermediate output of
interpretation, not a hand-written prerequisite. Humans label evaluation sets
and decide disputed interpretations; they need not read every incoming record.

Use an existing supported agent runtime with governed MCP tools. Do not build
an LLM gateway or permanently running Python model client for the first pilot.
A manually invoked agent run is sufficient to test usefulness. Runtime choice,
credentials, scheduling and data-processing boundaries need acceptance before
unattended operation. Do not route internal content to an unapproved service.

## Six responsibilities

1. Classify each meaningful part of an update: concern/obligation, evidence,
   decision, correction, handover or background. Mixed records remain mixed;
   original records are preserved and each extracted claim cites its source.
2. Retrieve and propose the owning deliverable by objective and accountable
   domain. A host, product or matching title is only a discovery hint.
3. Connect evidence, corrections, supersession, incident recovery, prevention
   and dependencies. Distinguish an interpreted relationship from one already
   present in DocPlane. Do not invent semantic reference types.
4. Reconcile new information with prior workstream understanding, preserving
   contradictions and uncertainty. Later timestamps alone do not supersede.
5. Propose filing, metadata, links or separate trackable obligations. Batch by
   independently acceptable deliverable; do not create an initiative per log.
6. Assemble a next-agent briefing and identify what is needed for acceptance.
   A repair, merged PR, deployment and successful soak are distinct evidence.
   An activity named RESOLUTION is not proof the entire initiative is complete.

## One bounded run

1. Establish an explicit scope: initiative IDs, relevant capture statuses,
   collection time, source authority and enumeration limitations. Discovery
   summaries are hints; fetch full records before making interpretations.
2. Read new/changed records and unresolved prior proposals. For the first pilot,
   use a frozen scope; do not claim complete global incremental coverage.
3. Extract claims with evidence locators and uncertainty. Record descriptions
   of decisions/assignments separately from verified decisions/assignments.
4. Search related initiatives using domain, objective and distinctive terms.
   Follow existing source-capture, activity, founding-capture and dependency
   relationships. Read plausible candidates before proposing placement.
5. Test each proposed interpretation against counterevidence: later updates,
   different ownership, partial acceptance, rejected prior proposals and the
   authoritative register where one exists. Stop retrieval at a documented
   budget; mark the result incomplete rather than treating no match as absence.
6. Produce a delta against the prior briefing: meaningful changes, proposals,
   unresolved questions, and the next useful action. Do not publish another
   report merely because a run occurred. No change is a valid result.
7. Validate provenance and output structure; submit proposed discrepancies to
   the existing ledger. Keep an interpretation checkpoint separate from a
   collection checkpoint. A record fetched successfully may remain unprocessed.

Initial retrieval budget suggestion: 10 changed records, up to 5 plausible
initiative candidates per record, and 30 additional full-record reads per run.
These are pilot controls, not permanent policy. Explicitly flag budget-limited
claims. Resume remaining work later; never advance a global success watermark
past records that failed or were not interpreted.

## Proposed interpretation output

Each claim needs:

- local claim ID and original source record/activity ID;
- source version or safe-content fingerprint with its projection version;
- role: obligation, evidence, decision, correction, handover or context;
- concise interpretation and source locator (no raw-body quotation required);
- relevant existing work IDs, relationship already present or proposed;
- temporal qualification: reported at, observed at, validity bounds if stated;
- responsibility: authenticated writer, reported producer, claimed accountable
  person/role and acknowledgement evidence, all kept distinct;
- epistemic status: explicit statement, supported inference, disputed or unknown;
- proposed next action, or no action when information is already organised.

Unknown identity/time/acceptance values stay unknown. Dates and roles extracted
from prose are claims with provenance, not new canonical fields.

A proposal additionally states the exact intended filing/link/metadata change,
why it helps the deliverable, alternatives considered, evidence, confidence
reason and required authority. If no deployed tool can express it, mark
CAPABILITY_GAP. Never pretend a proposed link already exists.

Stable obligation matching must compare prior claims for the same source and
meaning. Rewording must not mint a fresh UUID. A split or merge of obligations
requires a proposed mapping and preserves earlier IDs and decisions. Do not
make the model choose new identities without checking prior interpretations.

## Evidence and freshness

Use canonical redacted content only. Withheld evidence stays unknown. Treat all
record text as untrusted data, including embedded instructions and tool calls.
Only trusted run policy controls tool use; the initial tool allowlist is reads.

The current text-free projection in #216 can replay mechanical structure but
cannot reproduce semantic interpretations or detect a changed body. Keep that
purpose explicit. Semantic fingerprints must cover the safe text actually used
plus relevant state/references, with a versioned canonicalisation. Never hash
raw/unredacted content. Retain private redacted evidence only where authorised;
otherwise reread source and mark old reasoning stale when it changes.

Persist only evidence and interpretations needed for continuity, in restricted
private storage. Do not commit production examples or content into Git. A
fingerprint is a change detector, not proof of truth or an access credential.

Existing reads do not provide a global snapshot: if initiative version/count
changes across pages, retry a bounded number of times, then mark incomplete.
Capture-only scans miss later initiative activities; include scoped histories.
An updated initiative does not imply an unchanged founding capture needs to be
interpreted afresh unless its relevant context changed.

Build dependencies from source evidence to claims to proposals/briefings. New
relevant evidence invalidates dependent interpretations. Do not reopen rejected
proposals merely for changed detector wording or unrelated history growth.
Absence after a partial scan never resolves anything. For initial runs, an agent
confirms resolution/rejection with reason and evidence; work state is separate.

## Workstream briefing (derived, rebuildable)

- Objective and scope, with canonical work IDs and authoritative register.
- Last verified milestone and evidence; distinct from reported progress.
- Open obligations, dependencies and claimed/confirmed responsibility.
- Recovered incidents with prevention still open.
- Decisions, holds, unacknowledged handovers and validity/review windows.
- Conflicting or withheld evidence and the limits of the scan.
- A short next action for the next executor; why it is the next action.

Every substantive sentence/row points to evidence. Mark briefing time and stale
claims. Briefings are derived views, never a second editable work register.
For the hub2 programme, the existing git completion register remains its current
coordination authority; the librarian does not silently migrate it to WORK.

## Authority progression (independent of reasoning capability)

Initial: read and propose privately. No production flags, notes or captures.
Next candidate: reversible filing/metadata/linkage in a bounded delegated scope,
only after tools exist and accuracy is reviewed. Preserve originals and audit.
Separate approval: creating trackable items, assignment, resolving disputed
relationships, lifecycle transitions, completion and publishing policy.
Never infer runtime approval from a WORK record or librarian proposal.

## Acceptance and measures

Run the synthetic cases in scenarios.json first. Then use private held-out real
records interpreted without the old manifest/labels, with an independent review
of obligations and evidence. Review samples of records receiving no proposals,
not just flagged records, to measure misses.

Measure separately: obligation extraction precision/recall, proposed placement,
relationship correctness, unsupported state/ownership claims, missed residuals,
review effort, retrieval cost, repeat-run stability and briefing usefulness.
Report counts, uncertainty and disagreements, not one headline accuracy score.
Do not tune on a sample and then describe it as held out.

Go to a manually reviewed live run only after all proposed claims are traceable,
all known partial-outcome cases avoid false closure, no unsafe action occurs,
and the held-out review shows useful organisation with acceptable review effort.
Reviewer and Charles agree the practical threshold from those results before
scheduling. Do not add schema solely to make a weak detector appear certain.

## Ownership of this design

Codex owns this interpretation/evaluation design. Valteri owns #216 and its
runnable integration, including safe-read transport and private acceptance on
VM8555. Sergio's accepted MCP release is not reopened by this design.

## Reconciled additions (proposed update from the #216 work; the sections above are unchanged)

Status: proposal. These additions record points agreed while reconciling this design with the #216 safe-transport and
ledger work. Runnable pieces (interpretation output schema and validator, `sem-v1` evidence versions) live in #216; this
document stays the prose and evaluation authority.

### Tracking, coverage and registers

*Tracked* means an identifiable work item records the obligation with a stated objective and a stated disposition
(accepted, planned, assigned or backlog). A trackable obligation may equally live in an entry of a register that the run
was explicitly told is authoritative, as the hub2 programme's git register is for its programme. Whether the tracking item
**actively covers** the obligation is a separate judgement per target. An abandoned, completed or stale tracker stays
visible in the interpretation and the briefing; its state never erases the relationship. A report that merely restates
the item is *mentioned only*, and a semantic match without a stored link is a *candidate*. Missing a link means coverage is
unconfirmed, never that nothing exists.

### Cache key and dependencies

An interpretation is cached by the fingerprint of its source evidence **and** the fingerprints of the linked records it
relied on, plus the interpreter, prompt and output-schema versions. A change in linked evidence invalidates the
interpretation of an unchanged record. Unrelated history growth does not. Fingerprints cover redacted text actually used
(plus relevant scalar state) under a versioned canonicalisation, consistent with the freshness section above.

### Confirmation is qualified by source

A status may be marked as confirmed *now* only with its source: WORK state read, DocPlane page read, or MODEL read. A WORK read
confirms what was recorded and the recorded state. It never confirms that an external action (merge, deployment,
host change) occurred; those carry a named verifier and remain unverified until checked there.

### Correction versus contradiction

A later record **retracts** an earlier claim only by explicit statement (or a stored structural link) that names the claim
on both sides; a later timestamp is never enough. Otherwise two conflicting claims are an **unresolved contradiction**.
Both records, both claims and their relationship are preserved either way, and the briefing lists the two classes separately.

### Id prefixes

A short id cited in text resolves only inside an explicit, enumerated scope, with exactly one candidate and confirmation by
a full-id read. Otherwise it stays unresolved and nothing is built on it. No index is part of this phase.

### Evaluation method

Held-out review uses two independent reviewers who finish their own extraction before seeing the interpreter's items or
each other's output, an arbiter who inspects source evidence for disputed items only, and an unfiltered random control
sample mixed in without being identified. Only genuine product or ownership decisions go to the people; technical
classification disputes are settled by the arbiter or reported as unsettled. The synthetic scenarios gained seven cases
(tracked but abandoned, register-tracked, repeat is not tracking, dependency invalidates cache, external claim
qualification, retraction versus contradiction, prefix resolution); the original six are unchanged.

### Interpretation discipline (added after…427 tokens truncated…  before it was written.

### Backlog guardianship

DocPlane WORK is the master backlog; the librarian guards it. Private indexes, briefings and interpretations are rebuildable aids and never
another authoritative list, and the librarian's intake-processing status is kept separate from a record's own lifecycle.

* **Intake evaluation of everything incoming.** Every new capture and every new activity receives one evaluation against existing canonical
  work, with one of these dispositions: contributes evidence or progress; adds an obligation to an existing deliverable; warrants a new
  deliverable; duplicates, corrects or supersedes earlier information; or remains uncertain.
* **Search beyond the immediate workstream, completed work included.** A record may be called new only after a recorded search that went past
  its own workstream and included completed work, with the search's limits stated. *Related* and *same obligation* are different relations:
  a related new action stays a distinct, independently acceptable obligation inside the coherent group; only a same-obligation restatement is
  consolidated, and then every source is kept.
* **Processing coverage.** The run states which records were incoming and every one has an evaluation; totals must equal the entries. A failed
  or withheld read, and a placement that cannot be settled, remain visible as such and are never counted as evaluated. Earlier placements are
  re-evaluated when later evidence changes their meaning.
* **Lifecycle is not processing.** An INBOX, attached, promoted or discarded record still needs its evaluation; a record's lifecycle never
  stands in for whether the librarian processed it.
* **Authority unchanged.** Proposals only for production; the corresponding triage and note verbs are named, never called.

One synthetic scenario (`mixed-intake-batch`) exercises all five kinds in a single batch: restated existing obligation, related new action,
duplicate, completion evidence and a genuinely new concern, checking each disposition and that no obligation is lost or duplicated.

### Corrections after the first filing rehearsal (as implemented in the #216 validator)

A filing rehearsal on a disposable copy exposed four defects. Each is now a validator rule with tests in #216. The rehearsal's own results were produced under the earlier rules and are kept unchanged.

1. **A new deliverable is never only an intake line.** `WARRANTS_NEW_DELIVERABLE` must reference at least one extracted obligation, or an explicit unresolved concern that points at source evidence in the incoming record. A concern has no action and no acceptance field, so nothing is manufactured. The disposition also requires placement `NO_HOME` and a recorded search beyond the workstream that included completed work.
2. **Proposed organisation is separate from canonical fact.** Records the librarian's own proposals wrote (a cross-reference note, a derived capture) are named in the run. A briefing status line or a relation that rests only on such records is refused, and the interpreter must not write scope claims of its own about an initiative: scope, objective and state come only from the initiative's own history.
3. **A cross-reference is not a rehoming.** Each intake entry has a placement (`HOME_FOUND`, `CROSS_REFERENCE_ONLY`, `NO_HOME`, `UNDECIDED`) and each proposal an effect (`REHOME`, `CROSS_REFERENCE_ONLY`, `RECORD_NEW`, `VERIFY`, `CLARIFY`). A rehoming proposal requires every evidence record's placement to be `HOME_FOUND`; a filing proposal means rehoming or a new record; a cross-reference cannot be an attachment.
4. **A shorter briefing must not imply completeness.** The briefing states `INCOMPLETE` and lists unresolved scope and omitted history whenever the run is incomplete or anything was not retrieved, shows that notice first, and links to the detailed backlog. A briefing cannot be `COMPLETE` while anything is omitted.

**Evidence status.** The reruns that check these fixes are regression evidence, not independent validation: the mixed-intake oracle was revised after its failure to accept an explicit concern in place of an obligation (the original result is kept), and the scope and rehoming rerun used a counterfactual input in which the cross-reference note carries the corrected wording.

**Known recall limitation (not fixed).** When the only record that states an action as an action is one the librarian itself wrote, the corrected rule leaves the interpreter with no obligation to extract. In the rerun this reduced the extracted obligations from seven to six: the follow-up was reported as an uncertain placement rather than lost silently. The cause is upstream: the original record had put that follow-up among suggestions. Other open limits: the briefing trades breadth for precision (a successor can miss older related items), there is no way to retire a proposal explicitly, and extraction recall is unmeasured.

### Contract as of #216 bc6a3d26 (additions from the independent held-out scoring)

Everything above stays. These points were added to the validator after an independent scoring of a held-out run; they are regression fixes, so the tests that cover them are not validation.

* **Purpose, obligation state and filing destination are three separate facts.** An intake entry states what the record is (completed evidence, progress evidence, obligation, decision, correction, handover, background). The obligation carries its own state and currency. Placement and targets say where it should be filed. A pinned authoritative-register row is a valid evidence target, so completed evidence with only a register home, or no home, stays completed evidence. A missing home never manufactures an open obligation, and a done obligation is not listed open.
* **Historical actions are reconciled against later evidence already retrieved.** An obligation lists `later_evidence`; with it, the status is not presented as plainly as-of-record, and while it is uncertain a home is only a candidate. A filing, linking or new-work proposal that rests on an uncertain current status must be provisional and held behind an existing verification proposal. Prevention work is tracked independently of recovery work, even when one record carries both.
* **Consolidation keeps every source and scope.** A consolidated obligation lists every source and each source's own scope (for example, which repository). One record may carry a consolidated action and a distinct new action; the distinct action then survives later triage of the other source.
* **Search scope comes from the read log.** Each input's scope is derived from what the run's read log shows was opened, always includes the input itself, and is refused if it names a record the log does not show or copies one generic list across inputs. Captures inspected, initiatives inspected and KNOW access are explicit per input; a KNOW search that did not happen must be listed as omitted and limits every conclusion that would depend on KNOW.
* **Inspect captures before new work or duplicate disposal.** A proposal that creates work or disposes of a duplicate is provisional unless relevant captures and initiatives were inspected.

Four synthetic scenarios were added for these points (`completed-evidence-register-row`, `uncertain-current-status-held-filing`, `mixed-record-consolidated-and-distinct`, `search-scope-from-the-read-log`); earlier scenarios are unchanged.

### Contract as of #216 bfe9a491 (additions from the bounded review of bc6a3d26)

Everything above stays. These are validator rules in #216 with synthetic tests; they are regression fixes, not validation.

* **A completed tracker cannot back a confident open claim.** An open or unclear obligation that is `TRACKED` while every identified tracker states done or complete is refused, unless each such tracker is marked as not covering the whole obligation (`PARTIAL_SCOPE` or `COMPLETED_WITHOUT_THIS`) and the added scope is stated in `scope_components`. The honest alternatives are a done-in-record obligation reconciled to later evidence, or an uncertain-current candidate. The check does not depend on the purpose label.
* **Missing placement does not reject a genuine decision or uncertain concern.** Only completed evidence is barred from carrying an open obligation.
* **`UNDETERMINED` purpose.** An unread or withheld record must have this purpose and an `UNCERTAIN` disposition; `UNDETERMINED` is allowed only with `UNCERTAIN`. The evaluations validator requires a non-`READ` input to be `UNCERTAIN` with low confidence.
* **Inspection counts must agree with reads.** Claimed captures and initiatives inspected cannot exceed the other records read in the document, nor what the read log shows opened.
* **Proposals carry structured `subjects`.** The records a proposal would file, link, consolidate or create work from. Filing, linking and consolidation checks apply to the subjects, not only to the evidence ids the agent lists: a subject that is uncertain or has an uncertain-current obligation makes the proposal provisional and held behind a proposal whose effect is `VERIFY`. Every subject must have been read.
* **Later evidence may cite a pinned authoritative-register row** (`register_id` plus `entry_ref`), only for a register the run declares.
* **Unavailable KNOW access needs a reason.** The run config requires `know_reason`; the briefing's omitted entry and the evaluations' "not searched: <reason>" statement must carry it.

**Known limits (unchanged or new).** KNOW is unreadable without a docs credential, so nothing may be said to be absent from it. A "same obligation" target must point at the home of a consolidated obligation; a record that owns no extracted obligation is expressed through the owning obligation's sources and per-source scope instead. Extraction recall is unmeasured. Structural checks do not show semantic accuracy.

### Run procedure: original logs and derived regression fixtures

The run procedure in #216 (`RUN-PROCEDURE.md`) is unchanged while #216 is pinned for review; this section is the authority for the distinction until it is folded in.

* **Original logs are evidence.** A run's output directory (interpretation, evaluations, read log, fingerprints, config, verification records, checksums) is written once, made read-only and never edited, even when a later contract rejects it.
* **Derived regression fixtures are separate.** Anything produced by migrating or correcting an original (a corrected interpretation, view or evaluations) goes in a new directory, labelled as derived regression work, naming the original it came from and the contract head that checks it. It is not a rerun, is not independent validation, and does not demonstrate interpreter accuracy.
* **A derived read log is the original's rows plus a config header** and the config hash on each row. Original rows are never changed or reordered, and the derived config records that it is derived.
* **Neither kind contains production content or credentials in Git.** Repository fixtures are synthetic only; private bundles stay outside the repository with a checksum file.

### Contract as of #216 34dc4c60 (final rules from the off-fabric review and the final check)

Everything above stays. These are validator and client rules in #216 with synthetic tests; they are regression fixes, not validation. This section supersedes the verification and count wording earlier in this document where they differ.

* **Verification proposals.** The VERIFY effect belongs only to `VERIFY` or `NEXT_ACTION` proposals. Such a proposal names no tool or a read-only read verb, never a triage, note or capture verb. A filing, linking or new-work proposal that rests on an uncertain current status must be provisional and held behind a verification proposal that addresses that uncertain subject (through its subjects or evidence); an unrelated verification does not satisfy the hold. A discard is never described as verification: it is a separate proposed action that needs separate approval and is held behind the check.
* **Provisional status is inherited.** A filing, link, consolidation or new-work proposal is provisional when a record it rests on has an intake entry that is not fully evaluated, or when no relevant capture or initiative was inspected for it.
* **Inspection counts follow the read type.** Claimed captures inspected cannot exceed the captures actually read (a work item or an initiative is not a capture); claimed initiatives inspected cannot exceed the initiatives read; the read log is checked the same way.
* **Fingerprints cover typed relationships.** An evidence version includes the typed references a record carries (references, source capture, founding or linked captures) as well as its text, so a changed reference invalidates dependent reasoning even when the body is unchanged. A record with no references keeps its earlier version.
* **Transport.** The MCP url must be `https`; plain `http` is accepted only to a loopback address with an explicit operator flag. Redirects are never followed for authenticated requests, so a bearer cannot reach another origin.
* **Initiative histories** are accepted only when every page has an integer `activity_count`, the version, count and initiative agree across pages, offsets and cursors are consistent, activity ids are unique and the final length equals the count. Otherwise the read fails and the run is incomplete.
* **Derived logs are labelled.** A derived regression log keeps every original row unchanged and starts with a header that says it is derived, names the source log and its checksum, and gives the original collection window and the derivation time. Original outputs stay immutable and separate from derived fixtures (see the run-procedure section above).

**Known limits.** Unchanged from above: no KNOW without a credential, no capture listing in the held-out run, extraction recall unmeasured, structural checks do not show semantic accuracy.
