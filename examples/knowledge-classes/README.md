# Knowledge-class examples

A small, generic corpus you can publish into any DocPlane installation
to see the knowledge-class system work end to end: the eight classes,
classification at page birth, the three-tier suggester, operator
curation, the bounded apply driver, and the audit surfaces that make
missing classification visible instead of silent.

Nothing here references any particular installation. The pages describe
a plausible small documentation platform — the kind of content most
teams have — so you can read them as templates for your own corpus.

## The eight classes

`knowledge_class` answers one question: *what kind of claim does this
page make on the reader?* It is orthogonal to publication state,
verification state, criticality, owner and workspace.

| Class          | The page is…                                    | Lifecycle character |
|----------------|--------------------------------------------------|---------------------|
| `ARCHITECTURE` | how the system is shaped, and why it holds       | evolves with the system |
| `OPERATION`    | how to do something safely (runbooks)            | corrected by exercise |
| `REFERENCE`    | stable facts you look up, not read through       | rots silently; verify on a clock |
| `POLICY`       | standing rules that must hold *now*, with IDs    | amended by decision, not by edit |
| `DECISION`     | a point-in-time choice: context, decision, consequences | immutable; superseded, never edited |
| `EVIDENCE`     | a record of what happened                        | immutable; superseded by the next record |
| `DESIGN`       | a proposal under exploration, not yet decided    | graduates into a DECISION or is dropped |
| `WORK_NOTE`    | live working state (the page form of active work) | archived when the work closes |

`NULL` is legal and visible: an unclassified page is governance debt the
Observatory counts, not a deployment failure. The database `CHECK`
constraint (migration 010) only guards the vocabulary against typos.

## What each example demonstrates

| Page | Class | How it gets classified |
|------|-------|------------------------|
| `architecture/service-topology.md` | `ARCHITECTURE` | at birth, via `CREATE_PAGE.knowledge_class` |
| `design/notification-routing.md` | `DESIGN` | at birth |
| `evidence/restore-drill-q2.md` | `EVIDENCE` | at birth |
| `decisions/adr-0001-single-writer.md` | — | suggester tier 2: ADR anatomy (title + Context/Decision/Consequences) → `DECISION` at 0.85, out-ranking the `decisions/` path convention |
| `operations/restore-from-backup.md` | — | suggester tier 2: runbook contract headings (Preconditions/Procedure/Success checks/Rollback) → `OPERATION` at 0.8 |
| `policies/credential-handling.md` | — | suggester tier 2: `INV-n` register entries → `POLICY` at 0.75 |
| `reference/environment-matrix.md` | — | suggester tier 3: `reference/` section convention → `REFERENCE` at 0.6 |
| `notes/search-upgrade-scratchpad.md` | — | suggester tier 1: inline `**Lifecycle:** ACTIVE` field → `WORK_NOTE` at 0.9 |
| `misc/team-glossary.md` | — | **no signal** — classified deliberately by a human (`REFERENCE` fits) via the Explore switcher |

The three unclassified-but-suggestible tiers are ordered strongest
signal wins: an explicit legacy lifecycle field beats document shape,
which beats path convention. The glossary is included precisely because
no tier catches it: curation, not the tool, is the authority, and some
pages are only ever classified by a person reading them.

One coherence rule to know: the classify verb enforces `WORK_NOTE` ⇔
WORK workspace, in both directions. The scratchpad is therefore seeded
into the `work` system workspace; every other example lives in
`reference`. A `WORK_NOTE` proposal for a page in the wrong workspace
is rejected with `WORKSPACE_KNOWLEDGE_CLASS_MISMATCH` — move the page
(or reconsider the class) rather than fighting the rule: it is the
work/know domain boundary doing its job.

## Seed the corpus

Credentials ride the environment, never argv:

```bash
export DOCPLANE_API=http://localhost:8080   # your routed site URL
export DOCPLANE_TOKEN=dp_...                # a named contributor token

python3 examples/knowledge-classes/seed_examples.py
```

The seeder publishes one ordinary change — CREATE_PAGE operations,
validate, publish — so the pages arrive with real receipts, audit
events and history. Pages that already exist are skipped, never
replaced; re-running converges to "nothing to do".

## Walk the classification workflow

**1. See the debt.** The Observatory structure report now counts six
unclassified pages, broken down by section, and Explore's
`knowledge_class=__missing__` filter lists them. Each shows a dashed
"set class…" chip.

**2. Suggest (read-only).**

```bash
python3 scripts/knowledge_class_suggest.py --out suggestions.json
```

Expect five proposals (one per tier as tabled above) and one
`no_signal` entry for the glossary. Nothing has been written.

**3. Curate.** Open `suggestions.json`, delete or amend anything you
disagree with. The file you feed forward is treated as
operator-approved — this step is the point, not a formality.

**4. Apply (bounded, replayable).**

```bash
python3 scripts/knowledge_class_apply.py suggestions.json --batch-limit 50
```

Every write goes through the same governed classify verb the UI uses:
optimistic-locked, idempotency-keyed, audited as `PAGE_CLASSIFIED`.
Re-run the identical command until `remaining == 0`; the closing run
must report `applied == 0` with everything `already_matched` — the
convergence proof.

**5. Finish by hand.** The glossary still shows as missing. Classify it
from the Explore switcher (with a reason), the way any no-signal page
in a real corpus gets classified: by someone who read it.

Afterwards the Observatory reports zero missing, and every
classification — born, suggested-then-approved, or hand-set — carries
an audit event saying who and why.

## Adapting this to your corpus

- Keep the class question in mind when splitting pages: a runbook that
  drifts into architecture explanation is two pages wearing one path.
- Classify at birth where you can (`CREATE_PAGE.knowledge_class`, or
  `write_doc(..., knowledge_class=...)` over MCP); backfill is for
  history, not a habit.
- Let `no_signal` pages accumulate visibly rather than guessing:
  the missing count is a reading list, and classifying a page as you
  read it is cheap.
