# Domain model implementation plan

Phased plan for [The four-domain model](DOMAIN_MODEL.md). Every phase is
independently shippable, additive-only, and ends with the compatibility gate
below passing. Issue #44's reorganisation programme continues throughout; no
phase mutates `docs.*` structure or competes with an active #44 batch.

## Compatibility gate (every phase)

1. Full existing test suite green, unmodified.
2. A migration round-trip test proves `working_state_identity` is byte-identical
   before and after the phase's migration on a seeded corpus — certification
   cannot drift because of a domain-model migration.
3. OpenAPI diff contains only additions (new paths, new schemas, new optional
   fields). CI fails on any removal or type change.
4. MCP tool inventory diff contains only additions.
5. `docplane.schema_migrations` checksum ledger extends; no prior migration
   file is edited.

## Phase 0 — ratify vocabulary (this change)

- Publish `DOMAIN_MODEL.md` and this plan.
- No runtime change. The decision that unblocks everything else is the
  vocabulary reconciliation: three vocabularies remain — `workspace_kind`,
  `knowledge_class`, `work_state` — and the markdown-parsed corpus
  "lifecycle" is marked deprecated.

## Phase 1 — work surface (no schema change)

The work domain exists; it lacks surface.

- `POST /api/v1/work/capture`: one-call idea capture minting a `BACKLOG`
  initiative in the WORK workspace with an auto-generated `initiative_key`,
  `kind` recorded in metadata, idempotency honoured via the standard header.
- MCP tools: `work_capture(text, kind="IDEA")`, `work_list(state?)`,
  `work_get(initiative)`, `work_note(initiative, text, type="NOTE")`,
  `work_transition(initiative, to_state, ...)` — thin clients of the
  existing `work-v1` endpoints, mirroring `mcp/tools/docs.py`.
- Dashboard Work view backed by data the overview endpoint already fetches
  (`work` and `maintenance` modules), presenting the queues: Now, Roadmap,
  Blocked, Soaking, Parked, Decisions needed, Ready to complete, Recently
  completed.
- Tests: capture contract (idempotent replay, key minting, workspace
  enforcement), MCP tool payload contract, dashboard proxy routes.

Exit: "save that idea in `.work`" is one agent call and one dashboard glance.

## Phase 2 — model genesis

- Migration `001_model_genesis.sql`: schema `model` with `entities`
  (`entity_id`, `entity_kind`, `entity_key`, `display_name`, `attributes`,
  `owner_principal_id`, `version`, timestamps; `UNIQUE (entity_kind,
  entity_key)`), `entity_links` (typed edges, closed relation vocabulary,
  self-link forbidden), `entity_page_links`, `generated_artifacts`.
- Routes: `GET/POST /api/v1/model/entities`, `GET
  /api/v1/model/entities/{entity_id}` (with links), `POST
  /api/v1/model/entities/{entity_id}/links`, `GET/POST
  /api/v1/model/artifacts`. Mutations require `Idempotency-Key` and
  `expected_version`, mirroring the work API. Events: `MODEL_ENTITY_CREATED`,
  `MODEL_ENTITY_LINKED`, `MODEL_ARTIFACT_DECLARED`.
- Redaction rule: `attributes` passes the canonical secret-shape scan;
  fail closed on secret-looking values.
- Tests: identity/link contracts, relation vocabulary closure, state-identity
  invariance, OpenAPI-additive check.

## Phase 3 — observe genesis

- Migration `002_observe_genesis.sql`: schema `observe` with `observations`
  (append-only, subject FK to `model.entities` or artifact, kind/outcome
  vocabularies, `source_fingerprint`, bounded payload) and `current_status`
  (latest per subject × kind, maintained in the ingest transaction).
- Routes: `POST /api/v1/observations` (batch-capable, idempotent),
  `GET /api/v1/observations` (cursor-paginated — apply the issue #56 lesson
  from day one), `GET /api/v1/model/entities/{entity_id}/status`.
- Freshness/drift evaluation: derived read comparing latest `GENERATION`
  fingerprint against latest observed source fingerprint; exposed on the
  artifact and entity status reads, never stored as authored state.
- Tests: append-only enforcement, projection correctness, derivation cases
  (fresh, stale, drifted, never-generated), pagination completeness.

## Phase 4 — cross-domain links and provenance

- Extend `work.initiative_links` CHECK constraints additively:
  `resource_type` += `MODEL_ENTITY`, `ARTIFACT`, `OBSERVATION`; `relation`
  += `IMPLEMENTS`, `CONCERNS`, `PRODUCES`, `UPDATES`. Existing rows and
  clients unaffected.
- Add `docs.pages.provenance` (`AUTHORED | GENERATED`, default `AUTHORED`,
  additive column; excluded from revision/state-identity computation, like
  other classification metadata under the issue #59 resolution).
- Validation: mutating a `GENERATED` page outside its declaring artifact's
  automation principal fails closed unless the declaration is retired first.
- Tests: constraint extension round-trip, provenance guard, identity
  invariance.

## Phase 5 — `tbls` exemplar end-to-end

- Named `AUTOMATION` principal for the generator; runner container invoking
  `tbls`, the canonical redaction transform, then the normal change contract.
- One work initiative (canary database first → soak → additional databases),
  model entities and artifact declaration, `GENERATION` observations per run,
  catalogue pages published `provenance=GENERATED` under a `model/` section
  created through the governed reorganisation contract after coordination
  with #44 sequencing.
- Acceptance: all four domains touched exactly as the decision describes;
  killing the source schema fingerprint match surfaces `DRIFT` on the entity
  status read; a failed generation surfaces `FAILED` without disturbing the
  published catalogue.

## Phase 6 — discovery, search and vocabulary closure

- Search index adds `workspace_key`, `knowledge_class`, `provenance`;
  results carry `domain` and are grouped by domain in agent responses;
  `domain=` filter.
- `/.well-known/docplane.json` advertises the four domains and their entry
  routes; `docplane://` URIs become domain-first with old forms as aliases.
- MCP: `know_*` aliases for the six document tools; `model_*` and
  `observe_*` read tools; `observe_report` write tool.
- Vocabulary closure: audit `docs.pages.knowledge_class` values, backfill,
  then add the CHECK constraint; remove the deprecated markdown-lifecycle
  parser from `corpus_structure.py` once the backfill is complete.

## Sequencing with issues #43 / #44

- Phases 0–4 touch no corpus content and may proceed at any time.
- Phase 5's `model/` section creation and catalogue page publication follow
  the same batch-eligibility rules #44 already applies (no shared resources
  with an active batch, certification `CURRENT` between batches).
- Phase 6's parser removal waits for the metadata backfill, which is ordinary
  `PATCH_METADATA` publication work and must respect #43's coordination
  register like any other metadata change.
