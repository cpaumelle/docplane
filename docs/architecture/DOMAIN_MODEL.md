# The four-domain model: work, know, model, observe

Status: **RATIFIED** architecture decision (issue #64, PR #70). Most of it is
now built; [Implementation status](#implementation-status) at the end says
which parts are and which are not. Principles are in
[Guiding philosophy](GUIDING_PHILOSOPHY.md). The original sprint plan is kept as
a [historical record](DOMAIN_MODEL_IMPLEMENTATION_PLAN.md).

DocPlane is knowledge management and agent coordination for people who build
and run systems with AI agents. The four domains are how it organises both:
**work** is where agents and people coordinate, and **know**, **model** and
**observe** are where durable facts live.

DocPlane remains one authority. This decision creates no new products, no
second database and no new permission tiers. It names four **domains of
intent** over the single PostgreSQL authority, applied as one shared
vocabulary at four layers:

- **Storage** — `work.*` and `docs.*` already exist and already are the work
  and know domains; they are not renamed. Two additive schemas are
  introduced: `model.*` and `observe.*`.
- **API** — existing routes are unchanged; new resource families get
  domain-named routers (`/api/v1/model/*`, `/api/v1/observations*`,
  `/api/v1/work/captures*`). No `/api/v2`.
- **Addressing** — `docplane://` URIs become domain-first
  (`docplane://model/service/docs-api`); old forms remain aliases.
- **Agent and human surface** — MCP tools are verb-prefixed by domain
  (`work_*`, `know_*`, `model_*`, `observe_*`; the six existing document
  tools keep their names as permanent aliases), and the dashboard grows one
  view per domain.

`wire` is a relationship verb inside `docplane.model`, not a fifth domain.
Change proposals, reorganisation plans, principals and events remain
cross-cutting control functions.

## The work domain — GTD-shaped

The `work.*` schema **is** the work domain; nothing is renamed. The existing
state machine turns out to map almost one-to-one onto Getting Things Done,
which DocPlane adopts as its work-management framework:

| GTD concept | DocPlane construct |
| --- | --- |
| Capture (zero-decision inbox) | `work.captures` — **new** |
| Clarify / triage | capture → initiative, note, or discard — **new** |
| Projects | `work.initiatives` |
| Next actions / doing | `ACTIVE` |
| Waiting-for | `BLOCKED` |
| Someday/Maybe | `BACKLOG` / `PARKED` |
| Weekly review | `review_due_at` + the queues view |

**Capture** is a lightweight inbox row — text, origin context, timestamp —
*not* an initiative. One agent call (`work_capture(text)`) or one API call
(`POST /api/v1/work/captures`), no title, no category, no state choice. The
capturing agent stamps origin context automatically (repository, session,
initiative in progress) so a triaged idea carries its own "where was I when
I thought this". **Triage** is the deliberate second act: promote to an
initiative, attach as an activity on an existing one, or discard guilt-free.
The dashboard Work view surfaces the inbox count and the weekly-review
queues (parked review due, soak review due, decisions needed), and applies a
soft WIP limit on `ACTIVE` so "Now" stays an honest word.

**Coordination.** Beyond capture and triage, the work domain is where agents
in different tools and sessions coordinate without a human relaying text:

- **Briefs and named updates.** An initiative carries a small set of named items
  (next actions, holds, deferrals, uncertainties, recorded completions). Agents
  read the brief before working and record outcomes against stable item IDs,
  bound to the brief they read. See [WORK briefs](WORK_BRIEF.md).
- **Safe reads.** Agents discover initiatives and read history and captures
  through redacted, allowlisted reads. See
  [Safe WORK reads](WORK_CAPTURE_READS.md).
- **Addressed inbox.** Scoped participants send durable, signed messages about
  one initiative; each message is also a WORK activity. See
  [Addressed WORK inbox](work-inbox.md).
- **The Librarian (designed, not finished).** A planned agent-plus-validator
  that proposes how to organise scattered WORK records into coherent
  deliverables. Only a read-only, manually run development worker exists. See
  [The Librarian](LIBRARIAN.md).

In every case a report or message is evidence, never authority: it cannot
approve a production action or mark work complete on its own.

**Closure gates** generalise the existing `promotion_state` pattern.
Finishing an initiative requires one disposition per durable domain:

- `know` — pages/decisions updated? (the existing `promotion_state`)
- `model` — did the structure change? cards updated, wires added, nodes
  retired?
- `observe` — is observability defined or updated? monitoring rules *and*
  runbook?

Each disposition is `UPDATED` (with typed links to what changed),
`NOT_REQUIRED` (with a one-line reason), or `DEFERRED` — and deferred mints
a visible gap in the coverage view rather than passing silently. The gate is
on the *question*, never on artifact existence: forcing artifacts at closure
is how empty runbooks get minted. `SOAKING` carries the intermediate gate:
soak success criteria must reference real monitoring (an alert, a dashboard
panel, a threshold), because a thing that cannot be observed cannot be
soaked. Dispositions are checkable, not honor-system: when a disposition
claims monitoring was updated, the monitoring importer's next run either
confirms the rule exists or surfaces the mismatch.

## The know domain — three shapes with three lifecycles

`knowledge_class` is the know facet (`ARCHITECTURE`, `OPERATION`,
`REFERENCE`, `POLICY`, `DECISION`, `EVIDENCE`, `DESIGN`); `WORK_NOTE`
remains the page form of work-domain state. Orthogonal and unchanged:
`publication_state`, `verification_state`, `criticality`, owner, workspace.

Doctrine "grows forever" only when three different shapes wear one name.
DocPlane separates them by lifecycle:

- **Decisions (ADR ledger).** Point-in-time and immutable: context,
  decision, consequences. Never edited, only superseded — a `SUPERSEDES`
  typed link keeps the chain navigable. A ledger is allowed to grow forever.
- **Invariants register.** Standing rules that must hold *now*, each with a
  stable ID (`INV-7`), a one-line statement, a link to the establishing
  ADR, and — the load-bearing part — a pointer to what *enforces* it: the
  test, CI check or validation rule that proves it. An invariant nobody
  enforces is a wish and gets demoted. That single rule keeps the register
  small: every entry costs an enforcement pointer.
- **Explanations.** Few, freely rewritten narrative pages whose job is to
  stay current, not complete.

**Runbook discipline** (`OPERATION` pages) applies four rules learned from
the empty-runbook failure:

1. Nothing auto-creates a runbook. Missing runbooks are recorded as *gaps*
   in the coverage view, triaged into work like any other debt.
2. Runbooks are born from real events. The session transcript of an actual
   remediation is the raw material; an agent drafts the runbook from what
   actually happened, and the second occurrence of a procedure is the
   signal to capture it.
3. A runbook counts only when it meets a content contract — preconditions,
   actual commands, expected output, success check, rollback — and is
   verified, at its strongest by having been *exercised* ("last successfully
   followed on ‹date›"). The existing expiry machinery decays unexercised
   runbooks back into gaps.
4. The requirement is scoped: `OPERATIONAL_CRITICAL` services and paging
   alerts require runbooks; everything else merely benefits. Surviving
   legacy stubs are archived, not filled in.

**Verification against reality** closes the staleness loop. The maintenance
queues (`unverified`, `verification_due`, `expired`, `outdated`) already
exist; the dashboard gains a freshness table (last updated / last verified,
by section) and a trigger. The trigger mints a **verification request** — a
work item scoped to a page or a whole section, carrying the page and its
linked model entities as briefing. DocPlane records the request and the
result; execution belongs to whatever agent picks it up. Outcomes flow
through existing contracts: facts hold → a verification against that exact
revision, with evidence in the notes (commands run, values seen); facts
drifted → a drafted correction through the normal change contract (direct
publication for minor factual fixes, pending change on critical pages).
Triggers are on-demand (including scoped pre-flight: "verify
`operations/proxmox/` before the upgrade"), graph ripple (a changed model
entity flags every page linked `DESCRIBES` to it), and expiry as a prompt.
Never a blind cron.

## The model domain — a harvested card index

The model is a card index of what the fabric structurally is: one card per
system, service, node, network, database, schema, API, route, interface or
artifact.

**Storage is generic; validity is contract-bound.** One table,
`model.entities`, mirrors the page identity contract deliberately:
`entity_id uuid` stable (like `resource_id`), `(entity_kind, entity_key)` as
the unique mutable handle (like `path`), `version` for optimistic
concurrency, `attributes jsonb` for kind-specific detail (bounded,
secret-scanned, fail-closed). Each kind's required attributes are published
as a JSON Schema checklist and enforced at the API — the same proven pattern
as `/api/v1/operation-contracts`. The kind vocabulary itself is enforced by
a database CHECK constraint, so adding a kind is an additive migration
extending that CHECK plus a card-contract entry — the compatibility
contract's textbook additive case (migration `008_monitor_rule_kind.sql` is
the exemplar; an earlier draft of this document called it a data change,
which implementation evidence overtook). The harvested starting set (method in
[the harvest survey design](CARD_TYPE_HARVEST.md); the deployment-specific
harvest report lives with the deploying fabric's records): `SYSTEM`, `SERVICE`,
`NODE`, `VM`, `SITE`, `NETWORK`, `DATABASE`, `SCHEMA`, `API`, `ROUTE`,
`DEVICE_MODEL`, `INTERFACE`, `ARTIFACT` — with `SITE` and `VM` added on
harvest evidence the original draft missed.

**Card types are harvested, not invented.** A survey initiative runs over
what the fabric already says about itself — compose files, systemd units,
proxy configs, DNS zones, monitoring targets, and the existing corpus prose
— and a kind earns an enforced checklist only when several real instances
fill it successfully. A thing with one instance stays a loosely-validated
card until it has siblings.

**Depth stops at the schema.** Databases and schemas are entities;
tables and columns live inside the generated catalogue, not as entities —
per-column cards would explode row counts and create a sync problem with
every product migration. Since initiative e004787b (decision D1,
2026-09-26) the generated catalogue has two faces from one introspection: the
human catalogue pages and a machine-readable **structured projection** stored
against the generated artifact (`model.artifact_projections`), which agents
query at table and column granularity through `/api/v1/model/structure`. It is
replaced wholesale by its generator and is never a set of MODEL entities.

**Typed links** carry the structure, each with a closed vocabulary:

- `model.entity_links` (entity → entity): `WIRED_TO`, `RUNS_ON`,
  `MEMBER_OF`, `DEPENDS_ON`, `EXPOSES`, `STORES_IN`, `GENERATED_FROM`,
  `WATCHES` (monitoring rule → watched entity).
- `model.entity_page_links` (entity ↔ page): `DESCRIBES`, `OPERATES`
  (runbook → service), `DECIDES`, `CATALOGUES`.
- `work.initiative_links` — extended additively with `resource_type` values
  `MODEL_ENTITY`, `ARTIFACT`, `OBSERVATION` and `relation` values
  `IMPLEMENTS`, `CONCERNS`, `PRODUCES`, `UPDATES`; the dangling
  `CATALOG_SNAPSHOT` hook becomes a real reference to a declared artifact.

**Generated artifacts and provenance.** `model.generated_artifacts`
declares each generator: name, version, config hash, source entity,
redaction policy, target pages. Pages gain `provenance ∈ AUTHORED |
GENERATED` (additive, default `AUTHORED`). A `GENERATED` page is published
through the normal validated change contract by an `AUTOMATION` principal —
versioned, certified, rollback-capable — and cannot be hand-edited while its
declaration stands; remediation is regeneration. Every tracked source gets a
**permanent thin presence page** ("DocPlane tracks this schema; 47 tables;
last regenerated ‹date›; owned by ‹service›") so tracking is visible to
humans, while the detailed catalogue republishes **only when the source
fingerprint changes** — a stable schema publishes nothing, ever. Authored
state stays change-only; there is no corpus-wide churn.

## The observe domain — the meter list, not the readings

Prometheus holds the readings; DocPlane holds the meter list. The observe
domain records **what is watched and what that means**, plus the thin
evidence ledger DocPlane needs for its own honesty. It is push-only and must
never become a monitoring system.

- **Monitoring rules are structure.** Alert rules and dashboards already
  live as config in git; an importer reads them and maintains rule entities
  wired `WATCHES` to the services they cover — the same generated-catalogue
  machinery as the schema catalogue. Nothing is hand-copied.
- **Coverage is the killer view**: services with nothing watching them,
  rules with no description, paging alerts with no runbook. Gaps rank by
  criticality and feed the work inbox. The importer records gaps; it is
  structurally incapable of creating stub pages.
- **Plain-English explanations.** Each imported rule gets an agent-generated
  one-sentence explanation ("fires when disk usage stays above 85% for 15
  minutes on any node"), bound to the rule's fingerprint exactly as page
  verification binds to a revision — edit the rule and the explanation goes
  stale and queues for regeneration. This is the human bridge: monitoring
  stays continuously matched to what a person can read and judge.
- **Evidence ledger.** `observe.observations` is append-only milestone
  evidence — deployed versions, generation results and source fingerprints,
  certification, soak readings, exercised-runbook attestations — with kind
  and outcome vocabularies and a `current_status` projection (latest row per
  subject × kind, maintained in the ingest transaction) so "what does
  reality currently show" is a cheap indexed read. Freshness and drift are
  *derived* by comparing fingerprints, never stored as authored state. No
  time series.

`docplane.events` remains the audit log of what DocPlane itself did;
observations record what external reality shows. They are not merged.

## The two reconciliation loops

The domains close into two loops that keep the whole corpus honest:

1. **Work → durable domains.** New truth is born in work; closure
   dispositions oblige it to flow into know, model and observe — or declare
   why not, visibly.
2. **Know/model → fabric.** Verification requests send agents to check
   recorded truth against reality, on demand or on graph ripple; evidence
   comes back bound to exact revisions, corrections flow through the normal
   change contract. Over time canonical facts migrate onto model cards, and
   verification splits into card-vs-fabric (mechanical) and page-vs-card
   (prose), making broad checking progressively cheaper.

## Answers to issue #64's six questions

1. **What are the domains?** All four surfaces with one shared vocabulary —
   storage schemas, API routers, URI scheme, agent tool prefixes and
   dashboard views — over one authority. Not four stacks, not `/api/v2`.
2. **Which page metadata is `know`?** `knowledge_class` (minus `WORK_NOTE`).
   Orthogonal: publication, verification, criticality, owner, workspace.
   `knowledge_class` gains a DB CHECK constraint after audit/backfill, and
   the markdown-parsed corpus "lifecycle" vocabulary is deprecated, leaving
   exactly three vocabularies: `workspace_kind`, `knowledge_class`,
   `work_state`.
3. **Identity and typed-link contracts?** Entities mirror the page contract
   (stable id + unique mutable handle + version); links live in the three
   closed-vocabulary tables above; observations are append-only with a
   current-status projection; artifacts are declared with provenance and
   fingerprint-derived freshness.
4. **Active Work workspace vs domain?** `work.*` is the domain; the WORK
   workspace stays an implementation detail for `WORK_NOTE` pages. The gap
   was surface (inbox, dashboard view, MCP tools), now specified.
5. **Search and MCP across domains?** Every result carries a `domain` field;
   agent responses group by domain; `domain=` filters; generated pages are
   badged; questions about current reality answer from `current_status`,
   never from page text. MCP verb prefixes make intent explicit; old tool
   names never break.
6. **Compatibility contract?** See below.

## Compatibility contract

Guaranteed for the entire introduction of the domain model:

- No change to `docs.*` tables, page identities, paths, revisions, the
  operation vocabulary, or the reorganisation workflow.
- Corpus state-identity computation is untouched; certification stays
  `CURRENT` across domain-model migrations and issue #44 batch receipts
  remain comparable.
- All schema changes additive: new schemas, new tables, nullable/defaulted
  columns, CHECK constraints extended only by adding values.
- No existing endpoint changes shape; new capability is new routes. MCP
  tool names never break.
- No retrospective renaming of receipts, evidence packages, workspaces,
  knowledge classes or event types produced by issue #43/#44 work.
- Issue #44's information-architecture programme continues against the
  current model; catalogue sections land through the same governed
  reorganisation contract, after #44's bounded move work, never concurrently
  with an active batch.

## Product surface

- **Work** — Inbox (untriaged captures), Now (`ACTIVE`, WIP-limited),
  Roadmap (`BACKLOG`), Blocked, Soaking, Parked, Decisions needed, Ready to
  complete (dispositions outstanding), Recently completed, Weekly review.
- **Know** — Architecture, Operations, Reference, Policies, Decisions
  (ADR chain), Invariants register, Evidence; the freshness table with
  per-page/per-section "verify against fabric" triggers; the existing
  authoring, trust and reorganisation functions.
- **Model** — Systems, Services, Nodes, Networks, APIs and routes,
  Databases and schemas, Dependencies and topology, Generated catalogues
  with presence pages.
- **Observe** — Coverage (unwatched services, rules without descriptions,
  alerts without runbooks), Deployment state, Certification, Drift,
  Freshness, Soak observations.

## Exemplars

**A — schema catalogue.** (The decision named `tbls`; the built generator
uses its own read-only `pg_catalog` introspection instead, see
[Schema catalogue](../operations/SCHEMA_CATALOGUE.md).) Work: one initiative (canary database →
soak → the rest). Model: database/schema entities, `STORES_IN` wires, one
artifact declaration. Observe: `GENERATION` observations with source
fingerprints; freshness/drift derived. Know: architecture page, redaction
policy, ownership decision, regeneration runbook (`OPERATES` the generator).
Catalogue pages publish `provenance=GENERATED`, regenerate only on
fingerprint change, behind permanent presence pages. The generator runs
under a named `AUTOMATION` principal and calls the canonical redaction
transform before content leaves the source boundary.

**B — monitoring meter list.** The rules importer reads Prometheus/Grafana
config from git, maintains rule entities `WATCHES`-wired to services,
generates fingerprint-bound plain-English explanations, and populates the
coverage view — gaps, never stubs. This is the second complete pass through
the same four-domain machinery, proving the pattern generalises.

### Coverage burn-down

Coverage truth remains a derived Observe projection; it is never replaced by
a hand-maintained queue. A bounded reconciler may project individual
description and paging-runbook gaps into reopenable Work items for humane
triage. The stable rule entity is the item identity: repeated reconciliation
converges, disappeared gaps resolve, and recurring gaps reopen. The default
batch creates at most ten new items while the coverage endpoint continues to
report the complete debt.

Runbook discipline still binds: a gap item does not create, stub or imply a
runbook. Runbooks are authored only from real operational events. Service
wiring is fixed upstream by a rule label wherever possible; a versioned,
operator-curated overlay may supply a `WATCHES` edge only for an unlabelled
rule and records `metadata.source=overlay` on that edge.

The scheduled meter-list run is the second reconciliation loop, not a blind
verification cron. It compares a Git authority with DocPlane's projection
through the same importer and receipts as a manual run. An unchanged tick
replays one fingerprint-bound nominal observation and makes no model, Know or
Work mutation. Work projection advances only when coverage changes or when an
operator explicitly requests reconciliation.

## Implementation status

Checked against the code on main. "Built" means a route, migration, script or
dashboard view exists; it does not mean every deployment runs it.

| Area | Status | Where |
| --- | --- | --- |
| Capture inbox and triage | Built | `work.captures` (migrations 001, 011); `/api/v1/work/captures*`; MCP `work_capture`, `work_inbox`, `work_triage` |
| GTD queues, soft WIP limit | Built | `/api/v1/work/queues` (`DOCPLANE_WIP_LIMIT`, default 5); dashboard Work board |
| Closure dispositions and soak gate | Built | migration 004; `WORK_CLOSURE_DISPOSITIONS_INCOMPLETE`, `WORK_SOAK_REQUIRES_OBSERVABILITY` |
| Briefs and named updates | Built | `/api/v1/initiatives/{id}/brief` and `/updates`; dashboard Work board |
| Safe WORK reads for agents | Built | `?view=summary`, `?activity_bodies=redacted`, capture reads |
| Addressed WORK inbox | Built, optional | migration 020; `/api/v1/work-inbox/*` |
| Librarian | Designed; read-only development worker only | [The Librarian](LIBRARIAN.md) |
| Model entities, links, page links, contracts | Built | migration 002; `/api/v1/model/*` |
| Generated artifacts, provenance, execution contracts, projections | Built | migrations 004, 006, 007, 015 to 018 |
| Artifact-scoped automation principals | Built | migration 019; `SCOPED_AUTOMATION` |
| Structured schema projection | Built | `/api/v1/model/structure*` |
| Observations and current status | Built | migration 003; `/api/v1/observations` |
| Meter list, coverage view, coverage burn-down | Built | `scripts/meter_list.py`; `/api/v1/observe/coverage*`; migration 009 |
| Verification requests and graph ripple | Built | migration 005; `/api/v1/verification-requests*` |
| Invariants register | Built, as a generated page from YAML | [Invariant register](../operations/INVARIANT_REGISTER.md) |
| `knowledge_class` CHECK constraint | Built | migration 010 |
| Domain-first `docplane://` URIs | Partly: new resource families use them; pages still use `docplane://pages/{id}` | `docs-api/app/*_api.py` |
| `domain` field and filter on search results | Not built | `docs-api/app/search.py` |
| `model_*` / `observe_*` MCP tools, `know_*` aliases | Not built, except `know_classify_doc` | `mcp/tools/` |
| ADR `SUPERSEDES` links for `DECISION` pages | Not built | — |
| Runbook content contract, `RUNBOOK_EXERCISED` gating, coverage gaps as inbox captures | Not built; coverage reports them as `follow_up` | `docs-api/app/observe_api.py` |
| Removal of the Markdown "lifecycle" parser | Not done | `docs-api/app/corpus_structure.py` |
