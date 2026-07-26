-- WP8 — trusted working-corpus certification.
-- One row describes the working and deployed documentation state.

CREATE TABLE IF NOT EXISTS docs.corpus_certification (
    id                          boolean     PRIMARY KEY DEFAULT TRUE,
    state                       text        NOT NULL,
    working_corpus_snapshot_id  text,
    deployed_build_snapshot_id  text,
    deployment_target_snapshot_id text,
    renderer_provenance         jsonb,
    publication_config_provenance jsonb,
    validator_contract_version  text,
    uncertifiable_target_pages  text[]      NOT NULL DEFAULT '{}',
    last_failure_rules          text[]      NOT NULL DEFAULT '{}',
    state_version               bigint      NOT NULL DEFAULT 0,
    last_certified_at           timestamptz,
    updated_at                  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT corpus_certification_singleton CHECK (id),
    CONSTRAINT corpus_certification_state_valid
        CHECK (state IN ('CURRENT', 'WORKING_AHEAD', 'DEPLOYMENT_FAILED', 'UNAVAILABLE')),
    CONSTRAINT corpus_certification_current_ids_match
        CHECK (state <> 'CURRENT'
               OR (working_corpus_snapshot_id IS NOT NULL
                   AND deployed_build_snapshot_id IS NOT NULL
                   AND working_corpus_snapshot_id = deployed_build_snapshot_id)),
    CONSTRAINT corpus_certification_ahead_ids_differ
        CHECK (state <> 'WORKING_AHEAD'
               OR (working_corpus_snapshot_id IS NOT NULL
                   AND deployed_build_snapshot_id IS NOT NULL
                   AND working_corpus_snapshot_id <> deployed_build_snapshot_id)),
    CONSTRAINT corpus_certification_usable_needs_provenance
        CHECK (state IN ('DEPLOYMENT_FAILED', 'UNAVAILABLE')
               OR (renderer_provenance IS NOT NULL
                   AND publication_config_provenance IS NOT NULL
                   AND validator_contract_version IS NOT NULL)),
    CONSTRAINT corpus_certification_state_version_nonneg CHECK (state_version >= 0)
);

CREATE OR REPLACE FUNCTION docs.corpus_certification_monotonic()
RETURNS trigger AS $$
BEGIN
    IF NEW.state_version <= OLD.state_version THEN
        RAISE EXCEPTION
            'corpus_certification.state_version must increase (old=%, new=%)',
            OLD.state_version, NEW.state_version
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS corpus_certification_monotonic_trg ON docs.corpus_certification;
CREATE TRIGGER corpus_certification_monotonic_trg
    BEFORE UPDATE ON docs.corpus_certification
    FOR EACH ROW EXECUTE FUNCTION docs.corpus_certification_monotonic();
