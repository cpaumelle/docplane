# Domain model implementation plan

Sprint sequence for [The four-domain model](DOMAIN_MODEL.md), governed by
the [guiding philosophy](GUIDING_PHILOSOPHY.md). Every sprint is a bounded,
independently shippable change that ends with the compatibility gate
passing. Issue #44's reorganisation programme continues throughout; no
sprint mutates `docs.*` structure or competes with an active #44 batch.

## Compatibility gate (every sprint)

1. Full existing test suite green, unmodified.
2. A migration round-trip test proves `working_state_identity` is
   byte-identical before and after the sprint's migration on a seeded
   corpus — certification cannot drift because of a domain-model migration.
3. OpenAPI diff contains only additions (new paths, new schemas, new
   optional fields). CI fails on any removal or type change.
4. MCP tool inventory diff contains only additions.
5. `docplane.schema_migrations` checksum ledger extends; no prior migration
   file is edited.

## Sprint 0 — ratify (this change)

Publish the philosophy, the decision and this plan. No runtime change. The
unblocking decisions: three vocabularies remain (`workspace_kind`,
`knowledge_class`, `work_state`), the markdown-parsed corpus "lifecycle" is
deprecated, GTD is adopted for work, and the meter-list scope of observe is
fixed.

## Sprint 1 — work surface: capture, triage, Work view

- Migration: `work.captures` — lightweight inbox rows (`capture_id`, `body`,
  `kind` ∈ `IDEA | NEXT_ACTION | FINDING | QUESTION`, origin context jsonb,
  `status` ∈ `INBOX | PROMOTED | ATTACHED | DISCARDED`, disposition link,
  author, timestamps). Additive; no change to `work.initiatives`.
- Routes: `POST /api/v1/work/captures` (zero-decision, idempotent),
  `GET /api/v1/work/captures?status=INBOX`, triage verbs
  (`.../promote` → mints an initiative, `.../attach` → activity on an
  existing one, `.../discard`).
- MCP tools: `work_capture(text, kind="IDEA")` (agent stamps origin
  context), `work_inbox()`, `work_triage(...)`, `work_list(state?)`,
  `work_get(...)`, `work_note(...)`, `work_transition(...)`.
- Dashboard Work view: inbox count, GTD queues (Now with soft WIP limit,
  Roadmap, Blocked, Soaking, Parked, Decisions needed, Recently completed),
  weekly-review surface from the existing `/api/v1/work/queues` and
  review-due fields.
- Tests: capture idempotency, triage state machine, origin-context capture,
  MCP payload contracts.

Exit: "save that idea in `.work`" is one call; triage is a deliberate,
pleasant dashboard act.

## Sprint 2 — model genesis with harvested card types

- On-fabric survey initiative (agent work, tracked in `work`): the
  executable brief is [Card-type harvest](CARD_TYPE_HARVEST.md). Read-only
  and dependency-free, it may start before any other sprint; a kind's
  checklist is ratified only when several real instances fill it, and the
  harvest data doubles as the initial card population census.
- Migration `00X_model_genesis.sql`: `model.entities` (stable id, unique
  `(entity_kind, entity_key)`, `version`, bounded secret-scanned
  `attributes`), `model.entity_links` (closed relation vocabulary including
  `WATCHES`, self-link forbidden), `model.entity_page_links`,
  `model.generated_artifacts`.
- Routes: `GET/POST /api/v1/model/entities`, entity detail with links,
  link creation, artifact declaration — mutations idempotent and
  version-bound, mirroring the work API. Per-kind attribute checklists
  published at `GET /api/v1/model/contracts` (the operation-contracts
  pattern), enforced on write.
- Events: `MODEL_ENTITY_CREATED`, `MODEL_ENTITY_LINKED`,
  `MODEL_ARTIFACT_DECLARED`.
- Tests: identity/link contracts, checklist enforcement (fail-closed on
  secret-shaped attributes), state-identity invariance.

## Sprint 3 — observe genesis: evidence ledger and current status

- Migration `00X_observe_genesis.sql`: `observe.observations` (append-only;
  subject FK to entity or artifact; `observation_kind` ∈ `DEPLOYED_VERSION |
  GENERATION | CERTIFICATION | FRESHNESS_CHECK | TEST | SOAK_READING |
  RUNBOOK_EXERCISED`; `outcome` ∈ `NOMINAL | DEGRADED | FAILED | UNKNOWN`;
  `source_fingerprint`; bounded payload) and `observe.current_status`
  (latest per subject × kind, maintained in the ingest transaction).
- Routes: `POST /api/v1/observations` (push-only, batch-capable,
  idempotent), `GET /api/v1/observations` (cursor-paginated from day one —
  the issue #56 lesson), `GET /api/v1/model/entities/{id}/status`.
- Freshness/drift derived on read by fingerprint comparison; never stored
  as authored state. No pull, no scraping, no time series — enforced by
  scope, documented as an invariant with this test suite as its
  enforcement pointer.
- Tests: append-only enforcement, projection correctness, derivation cases
  (fresh, stale, drifted, never-generated), pagination completeness.

## Sprint 4 — closure gates, cross-domain links, provenance

- Extend `work.initiative_links` CHECK constraints additively:
  `resource_type` += `MODEL_ENTITY | ARTIFACT | OBSERVATION`; `relation` +=
  `IMPLEMENTS | CONCERNS | PRODUCES | UPDATES`.
- Closure dispositions: alongside the existing `promotion_state` (know),
  add model and observe dispositions ∈ `UPDATED | NOT_REQUIRED | DEFERRED`
  with reason/links; `COMPLETE` requires all three answered; `DEFERRED`
  mints a coverage gap. `SOAKING` entry additionally requires soak criteria
  referencing monitoring (validated as a link or named rule).
- `docs.pages.provenance` (`AUTHORED | GENERATED`, additive, default
  `AUTHORED`, excluded from state-identity computation); validation rejects
  mutation of a `GENERATED` page outside its declaring artifact's
  automation principal unless the declaration is retired.
- Tests: gate refusal messages are structured and machine-readable (an
  agent blocked at closure learns exactly which disposition is missing),
  constraint round-trips, provenance guard, identity invariance.

## Sprint 5 — exemplar A: `tbls` schema catalogue

- Named `AUTOMATION` principal; runner container invoking `tbls`, the
  canonical redaction transform (`migration.redaction.redact`), then the
  normal change contract.
- One work initiative (canary database → soak with monitored criteria →
  remaining databases), database/schema entities, artifact declaration,
  `GENERATION` observations per run.
- Permanent thin presence page per tracked database; detailed catalogue
  pages `provenance=GENERATED`, republished **only on source fingerprint
  change**. The `model/` section lands through the governed reorganisation
  contract, sequenced after #44's bounded moves.
- Acceptance: a stable schema publishes nothing across repeated runs; a
  changed fingerprint regenerates exactly the affected catalogue; a failed
  generation records `FAILED` without disturbing the published catalogue;
  closure dispositions on the initiative all answered.

## Sprint 6 — exemplar B: monitoring meter list and runbook discipline

Shipped in the meter-list PR (first half):

- Rules importer reads Prometheus rule files from git: rule entities
  `WATCHES`-wired to services, descriptions/`runbook_url` annotations
  imported, fingerprint-bound plain-English explanations generated per rule.
  The rule files are authoritative across the whole lifecycle: edits update
  entities and replace `WATCHES` wires, removals retire entities (behind an
  explicit mass-retirement bound) and archive their pages, and target-set or
  generator-contract changes declare a successor artifact (migration 007).
- Coverage view (`GET /api/v1/observe/coverage`): unwatched services, rules
  without descriptions, paging alerts without a `runbook_url` annotation —
  ranked by `criticality` derived from `DESCRIBES`-linked pages. The
  response's `scope` block versions the surface explicitly. The importer
  records gaps and is structurally incapable of creating pages.

Runbook-discipline follow-up (second half, deliberately not in that PR —
the coverage `scope` block lists these as `follow_up`):

- Runbook content contract for `OPERATION` pages (preconditions, commands,
  expected output, success check, rollback); coverage counts only
  contract-meeting, verified runbooks — upgrading the annotation-presence
  check the shipped endpoint applies; `RUNBOOK_EXERCISED` observations
  record real use; expiry decays unexercised runbooks back to gaps.
- Coverage gaps feed the work inbox as deduplicated captures.
- Grafana provisioning/dashboards and `targets/` scrape-file enrichment.
- Stub cleanup: the corpus harvest confirmed the legacy `runbooks/` stub
  tree (162 pages) is already fully archived and the 35 active runbooks are
  substantial; remaining debt is coverage and alert→runbook linking, not
  stubs.
- Acceptance: coverage numbers are honest (no stub counts), a rule edit
  stales its explanation, a deferred runbook disposition appears as a gap.

## Sprint 7 — freshness surface and verification-on-demand

- Dashboard freshness table (last updated / last verified, by section) from
  the existing maintenance queues; per-page and per-section "verify against
  fabric" trigger.
- The trigger mints a **verification request**: a work item carrying the
  page(s) and linked entities as briefing. Agents execute; results return
  as evidence-bearing revision-bound verifications (existing
  `POST /api/v1/pages/{id}/verify`, notes carry commands run and values
  seen) or drafted corrections through the normal change contract
  (direct publication for minor fixes; pending change when `criticality`
  demands review).
- Graph ripple: a model-entity change flags pages linked `DESCRIBES` as
  verification candidates. Expiry remains a prompt only; no scheduled
  re-verification exists anywhere.
- Tests: request minting and scoping, ripple candidate generation, evidence
  round-trip, criticality-gated correction path.

## Sprint 8 — know spine and vocabulary closure

- ADR discipline for `DECISION` pages: immutable once published, `SUPERSEDES`
  typed links, chain navigation in the dashboard.
- Invariants register: consolidation, not greenfield — 43 invariant pages
  already exist under `control-plane/invariants/` and
  `control-plane/topology-invariants/` with a stable `i-<topic>-<n>` ID
  convention, which the register adopts. The work is unifying them into one
  ID-addressable register, linking each to its establishing ADR, and
  auditing enforcement pointers (test/CI/validation); entries without
  enforcement are visibly flagged for demotion. Existing review machinery
  drives periodic register review.
- Search/discovery: index `workspace_key`, `knowledge_class`, `provenance`;
  results carry `domain` and group by domain; `domain=` filter;
  `/.well-known/docplane.json` advertises the domains; `docplane://` URIs
  become domain-first with old forms as aliases; `know_*` MCP aliases plus
  `model_*`/`observe_*` read tools and `observe_report`.
- Vocabulary closure: audit and backfill `docs.pages.knowledge_class`, add
  the CHECK constraint, then remove the deprecated markdown-lifecycle
  parser from `corpus_structure.py`. Backfill is ordinary `PATCH_METADATA`
  publication work respecting #43's coordination register.

## Sequencing with issues #43 / #44

- Sprints 0–4 and 7 touch no corpus content and may proceed at any time.
- Sprint 5's `model/` section and sprint 6's stub-archive batches follow
  the same batch-eligibility rules #44 already applies (no shared resources
  with an active batch; certification `CURRENT` between batches).
- Sprint 8's backfill and parser removal respect #43's coordination
  register like any other metadata change.
