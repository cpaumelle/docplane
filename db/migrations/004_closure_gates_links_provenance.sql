-- Sprint 4 of the four-domain model (docs/architecture/DOMAIN_MODEL.md):
-- cross-domain links, closure dispositions, the soak observability gate and
-- page provenance. Additive under the compatibility contract: new defaulted
-- or nullable columns, and CHECK vocabularies extended ONLY by adding values
-- (each swapped constraint keeps its name and its new set is a strict
-- superset of the old — asserted by test_closure_gates_contract.py).

-- work.initiative_links vocabularies gain the cross-domain values; the
-- dangling CATALOG/CATALOG_SNAPSHOT hook keeps working unchanged.
ALTER TABLE work.initiative_links DROP CONSTRAINT initiative_links_relation_check;
ALTER TABLE work.initiative_links ADD CONSTRAINT initiative_links_relation_check CHECK (relation IN (
    'CONTEXT', 'EVIDENCE', 'DECISION', 'RUNBOOK', 'BLOCKED_BY', 'PROMOTES_TO',
    'CHANGE', 'CATALOG_SNAPSHOT',
    'IMPLEMENTS', 'CONCERNS', 'PRODUCES', 'UPDATES'));
ALTER TABLE work.initiative_links DROP CONSTRAINT initiative_links_resource_type_check;
ALTER TABLE work.initiative_links ADD CONSTRAINT initiative_links_resource_type_check CHECK (resource_type IN (
    'PAGE', 'INITIATIVE', 'CHANGE', 'CATALOG', 'EXTERNAL',
    'MODEL_ENTITY', 'ARTIFACT', 'OBSERVATION'));

-- Closure dispositions: finishing an initiative must answer, per durable
-- domain, "did it get its update from this work?". know already has
-- promotion_state; model and observe gain the same shape. NULL = unanswered.
ALTER TABLE work.initiatives
    ADD COLUMN model_disposition text CHECK (model_disposition IN ('UPDATED', 'NOT_REQUIRED', 'DEFERRED')),
    ADD COLUMN model_disposition_note text,
    ADD COLUMN observe_disposition text CHECK (observe_disposition IN ('UPDATED', 'NOT_REQUIRED', 'DEFERRED')),
    ADD COLUMN observe_disposition_note text,
    ADD COLUMN soak_monitoring_ref text;

-- Provenance: a GENERATED page is a certified derived artifact — remediation
-- is regeneration, not hand-editing. Excluded from state identity (which
-- hashes resource_id/path/nav_path/revision/status only), so this cannot
-- move working or deployed identities.
ALTER TABLE docs.pages
    ADD COLUMN provenance text NOT NULL DEFAULT 'AUTHORED' CHECK (provenance IN ('AUTHORED', 'GENERATED'));
ALTER TABLE docs.page_metadata_history
    ADD COLUMN provenance text;
