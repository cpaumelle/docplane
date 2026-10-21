# DocPlane Librarian — interpretation contract (proposed)

Status: design for review, not agent policy or a deployed service.
No new schema, relationship type, identity or mutation authority is established.
Complements worker PRs #215/#216; does not replace their transport or ledger.

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

### Interpretation discipline (added after the first manually reviewed runs)

* **Obligation, evidence, suggestion, background.** An obligation is something someone must do, decide or deliver, with its own acceptance
  condition. A reported result is evidence. An idea nobody committed to is a suggestion, even with a stated trigger ("if it recurs").
  Restating status is background. When unsure between obligation and suggestion, choose suggestion and record the uncertainty.
* **Split and consolidate.** Split only actions that can be independently accepted or closed. The same obligation stated in several
  records is one obligation that keeps every source reference.
* **Uncertainty survives review.** An obligation that independent review left unsettled or unarbitrated may back only verification or
  next-action proposals, never a confident filing, linking, metadata or consolidation proposal.
* **Scope exclusion never hides a correction.** Before a record is left out of a lane's interpretation, check whether it corrects,
  contradicts, supersedes or is cited by an in-scope claim. If so it is kept with the related claim and relation, and the lane difference
  is stated. Without an explicit retraction the relation is a contradiction or a semantic supersession, never a retraction. A first
  manually reviewed run missed exactly this: a record was set aside as another lane's and its different diagnosis of an in-scope failure was
  never linked.
* **Live state outruns captures.** A capture's account of a pull request or a register row is a historical claim; both can have moved by the time
  it is read. Reference conclusions are a comparison, not truth: in one comparison the reference reported a pull request open that had merged
  before it was written.

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
