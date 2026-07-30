-- Sprint 3 of the four-domain model (docs/architecture/DOMAIN_MODEL.md):
-- the observe domain — a thin, push-only ledger of milestone evidence about
-- external reality, plus a current-status projection so "what does reality
-- currently show" is a cheap indexed read. NOT a monitoring system: no pull,
-- no scraping, no time series; readings live in the tools built for them.
-- Additive only: no existing schema, table, column or constraint is touched.

CREATE SCHEMA observe;

CREATE TABLE observe.observations (
    observation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    seq bigserial UNIQUE,
    observed_at timestamptz NOT NULL DEFAULT now(),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    observer_principal_id uuid NOT NULL REFERENCES docplane.principals(principal_id),
    subject_entity_id uuid REFERENCES model.entities(entity_id),
    subject_artifact_id uuid REFERENCES model.generated_artifacts(artifact_id),
    observation_kind text NOT NULL CHECK (observation_kind IN (
        'DEPLOYED_VERSION', 'GENERATION', 'CERTIFICATION', 'FRESHNESS_CHECK',
        'TEST', 'SOAK_READING', 'RUNBOOK_EXERCISED')),
    outcome text NOT NULL DEFAULT 'NOMINAL' CHECK (outcome IN ('NOMINAL', 'DEGRADED', 'FAILED', 'UNKNOWN')),
    source_fingerprint text CHECK (source_fingerprint IS NULL OR source_fingerprint ~ '^[0-9a-f]{8,64}$'),
    summary text CHECK (summary IS NULL OR length(summary) <= 1000),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (pg_column_size(payload) <= 16384),
    idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
    UNIQUE (observer_principal_id, idempotency_key),
    CHECK ((subject_entity_id IS NULL) <> (subject_artifact_id IS NULL))
);
CREATE INDEX observations_subject_idx
    ON observe.observations(subject_entity_id, subject_artifact_id, observation_kind, observed_at DESC);
CREATE INDEX observations_seq_idx ON observe.observations(seq);

-- Append-only is an executable invariant, not a convention: the ledger can
-- never be updated or deleted through any code path, break-glass included.
CREATE FUNCTION observe.forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'observe.observations is append-only';
END;
$$;
CREATE TRIGGER observations_append_only
    BEFORE UPDATE OR DELETE ON observe.observations
    FOR EACH ROW EXECUTE FUNCTION observe.forbid_mutation();

-- Latest observation per subject x kind, maintained in the ingest
-- transaction (the docs.corpus_certification pattern, generalised). A
-- late-arriving observation older than the projected row never regresses it.
CREATE TABLE observe.current_status (
    status_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_entity_id uuid REFERENCES model.entities(entity_id),
    subject_artifact_id uuid REFERENCES model.generated_artifacts(artifact_id),
    observation_kind text NOT NULL,
    observation_id uuid NOT NULL REFERENCES observe.observations(observation_id),
    outcome text NOT NULL,
    observed_at timestamptz NOT NULL,
    source_fingerprint text,
    summary text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (subject_entity_id, subject_artifact_id, observation_kind),
    CHECK ((subject_entity_id IS NULL) <> (subject_artifact_id IS NULL))
);
CREATE INDEX current_status_entity_idx ON observe.current_status(subject_entity_id) WHERE subject_entity_id IS NOT NULL;
CREATE INDEX current_status_artifact_idx ON observe.current_status(subject_artifact_id) WHERE subject_artifact_id IS NOT NULL;
