# The four-domain model: work, know, model, observe

Status: **PROPOSED** — architecture decision for issue #64, converged through
owner review. Principles are in
[Guiding philosophy and objectives](GUIDING_PHILOSOPHY.md); the sprint
sequence is in the
[implementation plan](DOMAIN_MODEL_IMPLEMENTATION_PLAN.md).

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
as `/api/v1/operation-contracts`. Adding a kind is a data change, not a
migration.

**Card types are harvested, not invented.** A survey initiative runs over
what the fabric already says about itself — compose files, systemd units,
proxy configs, DNS zones, monitoring targets, and the existing corpus prose
— and a kind earns an enforced checklist only when several real instances
fill it successfully. A thing with one instance stays a loosely-validated
card until it has siblings.

**Depth stops at the schema.** Databases and schemas are entities;
tables and columns live inside the generated catalogue, not as entities —
per-column cards would explode row counts and create a sync problem with
every product migration.

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

**A — `tbls` schema catalogue.** Work: one initiative (canary database →
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
