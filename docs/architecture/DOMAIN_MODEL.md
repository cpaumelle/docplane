# The four-domain model: work, know, model, observe

Status: **PROPOSED** — architecture decision for issue #64. Ratification makes the
vocabulary below normative; the phased plan is in
[Domain model implementation plan](DOMAIN_MODEL_IMPLEMENTATION_PLAN.md).

DocPlane remains one authority. This decision does not create new products,
new databases or new permission tiers. It names four **domains of intent** over
the single PostgreSQL authority so a human or agent can address knowledge by
what they are trying to do:

| Domain | Question it answers | Agent phrasing |
| --- | --- | --- |
| `docplane.work` | What should happen or is happening? | "Save that idea in `.work`." |
| `docplane.know` | What should people and agents understand and rely on? | "Check `.know` for how that server is configured." |
| `docplane.model` | What is the fabric, structurally? | "Check `.model` for the schema or API route." |
| `docplane.observe` | What does reality currently show? | "Verify the deployed state in `.observe`." |

`wire` is a relationship verb inside `docplane.model` ("wire the service to its
database"), not a fifth domain.

Domains are **object types and views inside one DocPlane authority**. They are
not Markdown folders, not separate services, and not authorization boundaries.

## Answers to the six open questions

### 1. What are the domains, technically?

One shared vocabulary applied at four layers — but not four parallel stacks:

- **Storage**: one PostgreSQL schema per domain that owns structured state.
  `work.*` and `docs.*` already exist and already are the work and know
  domains; they are not renamed. Two additive schemas are introduced:
  `model.*` and `observe.*`.
- **API**: resource routes stay under `/api/v1/*`. Existing routes
  (`/api/v1/pages`, `/api/v1/initiatives`, …) are unchanged. New resource
  families get domain-named routers: `/api/v1/model/*` and
  `/api/v1/observations*`. There is no `/api/v2` and no per-domain API
  version.
- **Addressing**: the `docplane://` URI scheme (already used for initiatives)
  becomes domain-first: `docplane://work/initiatives/{id}`,
  `docplane://know/pages/{resource_id}`, `docplane://model/{kind}/{key}`,
  `docplane://observe/observations/{id}`. Old URIs remain valid aliases.
- **Agent surface**: MCP tools are verb-prefixed by domain — `work_*`,
  `know_*`, `model_*`, `observe_*`. The six existing document tools keep
  their names as permanent aliases of their `know_*` equivalents.
- **Dashboard**: four top-level domain views (see product surface below).

### 2. Which page metadata belongs to `know`, and what stays orthogonal?

`knowledge_class` is the know-domain facet. Its values map directly onto the
know product surface: `ARCHITECTURE`, `OPERATION` (runbooks/operations),
`REFERENCE`, `POLICY`, `DECISION`, `EVIDENCE`, `DESIGN`. `WORK_NOTE` is not a
know facet — it is the page form of work-domain state, and the existing rule
(WORK workspace ⟺ `WORK_NOTE`) already encodes that.

Orthogonal to the domain, unchanged: `publication_state`,
`verification_state`, `criticality`, accountable owner, workspace,
`metadata_version`. These are trust and lifecycle axes that apply to any page
regardless of domain, exactly as [Page trust](PAGE_TRUST.md) states.

Two reconciliations are required and are part of this decision:

- **`knowledge_class` gains a database CHECK constraint** matching the API
  vocabulary, after a data audit (it is currently nullable and unconstrained
  in `docs.pages`; only `trust_models.py` enforces it).
- **The markdown-parsed corpus "lifecycle" vocabulary is deprecated.**
  `corpus_structure.py` parses `**Lifecycle:** X` out of page bodies into a
  third vocabulary (`REFERENCE, OPERATION, ACTIVE, PAUSED, BACKLOG,
  ARCHIVED`) that mixes knowledge classes with work states. Structural
  signals should read page metadata, not body conventions. The parser remains
  as a legacy signal until the metadata backfill completes, then is removed.

After reconciliation exactly three vocabularies remain, one per concern:
`workspace_kind` (classification), `knowledge_class` (know facet),
`work_state` (work state machine).

### 3. Identity and typed-link contracts

**Model entities** mirror the page identity contract deliberately:

- `entity_id uuid` — stable identity, survives renames (like
  `docs.pages.resource_id`).
- `(entity_kind, entity_key)` — unique mutable handle (like `path`).
  `entity_kind` ∈ `SYSTEM | SERVICE | NODE | NETWORK | DATABASE | SCHEMA |
  API | ROUTE | INTERFACE | ARTIFACT`. `entity_key` follows the
  `workspace_key` pattern (`^[a-z0-9][a-z0-9_.-]{0,126}$`).
- `version bigint` — optimistic concurrency (like initiatives).
- `attributes jsonb` — kind-specific detail, bounded, never secret-bearing.

**Typed links** live in three places, each with a closed relation vocabulary:

- `model.entity_links` — entity → entity structural edges:
  `WIRED_TO` (runtime connection), `RUNS_ON`, `MEMBER_OF`, `DEPENDS_ON`,
  `EXPOSES`, `STORES_IN`, `GENERATED_FROM`.
- `model.entity_page_links` — entity ↔ know page:
  `DESCRIBES`, `OPERATES` (runbook → service), `DECIDES`, `CATALOGUES`.
- `work.initiative_links` — already exists; its enums are extended additively
  with `resource_type` values `MODEL_ENTITY`, `ARTIFACT`, `OBSERVATION` and
  `relation` values `IMPLEMENTS`, `CONCERNS`, `PRODUCES`, `UPDATES`. The
  dangling `CATALOG` / `CATALOG_SNAPSHOT` hook becomes real: it points at a
  `model.generated_artifacts` row.

**Observations** are append-only rows in `observe.observations`:
`observation_id`, `observed_at`, `observer_principal_id`, subject
(`entity_id`, or artifact), `observation_kind` ∈ `HEALTH | DEPLOYED_VERSION |
CERTIFICATION | DRIFT | FRESHNESS | GENERATION | TEST | SOAK_READING |
MEASUREMENT`, `outcome` ∈ `NOMINAL | DEGRADED | FAILED | UNKNOWN`,
`source_fingerprint`, bounded `payload jsonb`. The relation to the model is
implicit and fixed: an observation `OBSERVES` its subject entity.

A projection table `observe.current_status` (latest row per subject ×
observation kind, maintained in the ingest transaction, following the
`docs.corpus_certification` singleton pattern) makes "what does reality
currently show" a cheap indexed read instead of a scan.

**Generated artifacts** are declared in `model.generated_artifacts`:
`artifact_id`, unique `artifact_key`, `generator_name`, `generator_version`,
`config_hash`, `source_entity_id`, redaction policy reference, and target
page paths. Freshness and drift are **derived**, not stored as authored
state: compare the latest `GENERATION` observation's `source_fingerprint`
with the latest observed source fingerprint.

`docplane.events` remains the audit log of what DocPlane itself did.
Observations are first-class records of what external reality shows. The two
are not merged: events answer "who changed what, when"; observations answer
"what is true out there, as of when".

### 4. Active Work workspace vs the work domain

The `work.*` schema **is** the work domain. Nothing is renamed or migrated.
The WORK workspace remains an implementation detail: it exists to classify
`WORK_NOTE` pages attached to initiatives, not to define the domain. What is
missing is surface, not model — the work domain gets:

- a dedicated dashboard Work view (the overview endpoint already fetches
  `/api/v1/work/queues`; no new data path is needed);
- MCP tools (`work_capture`, `work_list`, `work_get`, `work_note`,
  `work_transition`) — the current MCP surface is documents-only;
- a **one-call capture contract**, because the highest-friction moment is
  saving an idea without leaving flow: `POST /api/v1/work/capture` takes
  `{summary, detail?, kind: IDEA | NEXT_ACTION | FINDING | QUESTION}` and
  mints a `BACKLOG` initiative (auto-generated `initiative_key`, WORK
  workspace, origin recorded in metadata). The MCP form is
  `work_capture(text)` — a single argument, nothing to decide mid-flow.
  `BACKLOG` is the parking lot for new ideas; `PARKED` (which requires a
  reason and review date) stays reserved for deliberately shelved in-flight
  work.

### 5. Search and MCP across domains

Cross-domain results must never let active intent, durable knowledge and
observed reality blur into each other:

- Every search result carries a `domain` field, and agent-facing search
  responses group results by domain. A `domain=` filter narrows scope.
- The search index (currently path/title/content only) additionally indexes
  `workspace_key`, `knowledge_class` and provenance so the domain can be
  computed at index time, and results can badge `GENERATED` pages and
  verification state.
- Questions about current reality are answered from `observe.current_status`,
  never from page text. Search may return an observe *pointer* (the entity
  and its current status), but freshness comes from the projection.
- MCP domain prefixes make intent explicit at the tool boundary: `work_*`
  writes intent, `know_*` reads/writes durable knowledge, `model_*` reads
  structure, `observe_*` reads/reports reality. Existing tool names remain
  permanent aliases.

### 6. Compatibility contract (protecting issue #44 and existing clients)

The following are guaranteed for the entire introduction of the domain model:

- No change to `docs.*` tables, page identities, paths, revisions, the
  operation vocabulary, or the reorganisation plan workflow.
- Corpus state-identity computation is untouched: adding `model.*` and
  `observe.*` schemas cannot move `working_state_identity` or
  `deployed_state_identity`, so certification stays `CURRENT` across the
  migrations and #44 batch receipts remain comparable.
- All schema changes are additive: new schemas, new tables, new nullable or
  defaulted columns, enum CHECK constraints extended only by adding values.
- No existing endpoint changes shape. New capability is new routes.
- No retrospective renaming of receipts, evidence packages, workspaces,
  knowledge classes or event types produced by #43/#44 work.
- MCP tool names never break: new names are additions, old names are aliases.
- The #44 information-architecture programme continues against the current
  model; the MkDocs tree remains governed by #44's approved taxonomy. Where
  generated model catalogues publish pages, they land in a `model/` section
  added through the same governed reorganisation contract, after #44's
  bounded move work, never concurrently with an active #44 batch.

## Provenance: authored vs generated pages

Pages gain a `provenance` classification ∈ `AUTHORED | GENERATED` (default
`AUTHORED`; additive column). A `GENERATED` page:

- is published through the normal change contract by an `AUTOMATION`
  principal, so it is versioned, validated, certified and rollback-capable
  like any page;
- is declared by exactly one `model.generated_artifacts` row;
- must not be hand-edited: remediation is regeneration from the authoritative
  source. Reverse-import from rendered output is prohibited. Validation
  rejects a non-owner mutation of a `GENERATED` page unless the artifact
  declaration is first retired;
- is search-badged so humans know they are reading a certified derived
  artifact, and agents know the structured form is available via
  `/api/v1/model/*`.

This is the honest shape of machine-facing knowledge: a human rarely browses
a schema catalogue, but an agent planning a migration reads it constantly.
Both get the same certified truth — the human through a rendered page, the
agent through structured entities — generated from one source.

## Product surface

The dashboard grows from four views (Overview, Authoring, Reorganisation,
Changes & versions) to four domain views plus the existing control functions:

- **Work** — Now (`ACTIVE`), Roadmap (`BACKLOG`), Blocked, Soaking, Parked,
  Decisions needed (`DECISION_REQUIRED` activities), Ready to complete,
  Recently completed. All of this is served by the existing
  `/api/v1/initiatives` and `/api/v1/work/queues` endpoints today.
- **Know** — Architecture, Operations, Reference, Policies, Decisions,
  Evidence (facets of `knowledge_class`), with the existing authoring,
  trust/maintenance and reorganisation functions beneath it.
- **Model** — Systems, Services, Nodes, Networks, APIs and routes, Databases
  and schemas, Dependencies and topology, Generated catalogues.
- **Observe** — Health, Deployment state, Certification, Drift, Freshness,
  Tests and verification, Soak observations — read from
  `observe.current_status` plus the existing certification singleton.

Change proposals, reorganisation plans, principals and events remain
cross-cutting DocPlane control functions, not a fifth domain.

## First exemplar: automated `tbls` schema catalogue

The `tbls` integration is the first end-to-end implementation, touching all
four domains exactly once:

- **work** — one initiative tracks the rollout: supported databases,
  ownership, redaction and secret-safety policy, generator version and
  configuration, publication design, canary and soak, future improvements.
  Its links: `CONCERNS` the database entities, `PRODUCES` the artifact,
  `UPDATES` the runbook page.
- **model** — entities for each database and schema (`DATABASE`, `SCHEMA`
  kinds; tables/columns/constraints in bounded `attributes`), `STORES_IN`
  edges from owning services, one `generated_artifacts` declaration
  (`GENERATED_FROM` the schema entity) naming the target catalogue pages.
- **observe** — each run reports a `GENERATION` observation carrying the
  source schema fingerprint and generated artifact identity; failures report
  `outcome=FAILED`. `FRESHNESS` and `DRIFT` are derived by comparing the
  latest generation fingerprint with the latest observed source fingerprint.
  Publication of the catalogue pages produces the ordinary certification
  evidence.
- **know** — hand-authored pages: schema-catalog architecture
  (`ARCHITECTURE`), redaction policy (`POLICY`), ownership model
  (`DECISION`), regeneration and recovery runbook (`OPERATION`, linked
  `OPERATES` to the generator service entity).

The generator runs under a named `AUTOMATION` principal, calls the canonical
redaction transform (`migration.redaction.redact`) before any content leaves
the source boundary, and publishes catalogue pages with
`provenance=GENERATED` through the normal validated change contract.
