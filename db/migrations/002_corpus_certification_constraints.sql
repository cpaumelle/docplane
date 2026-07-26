-- WP8 — state-specific integrity for the certification singleton.

ALTER TABLE docs.corpus_certification
    DROP CONSTRAINT IF EXISTS corpus_certification_no_empty_identities;
ALTER TABLE docs.corpus_certification
    ADD CONSTRAINT corpus_certification_no_empty_identities CHECK (
        coalesce(working_corpus_snapshot_id, 'x') <> ''
        AND coalesce(deployed_build_snapshot_id, 'x') <> ''
        AND coalesce(deployment_target_snapshot_id, 'x') <> ''
    );

ALTER TABLE docs.corpus_certification
    DROP CONSTRAINT IF EXISTS corpus_certification_current_no_active_deployment;
ALTER TABLE docs.corpus_certification
    ADD CONSTRAINT corpus_certification_current_no_active_deployment CHECK (
        state <> 'CURRENT' OR deployment_target_snapshot_id IS NULL
    );

ALTER TABLE docs.corpus_certification
    DROP CONSTRAINT IF EXISTS corpus_certification_failed_keeps_diagnosis;
ALTER TABLE docs.corpus_certification
    ADD CONSTRAINT corpus_certification_failed_keeps_diagnosis CHECK (
        state <> 'DEPLOYMENT_FAILED'
        OR (working_corpus_snapshot_id IS NOT NULL
            AND deployed_build_snapshot_id IS NOT NULL
            AND deployment_target_snapshot_id IS NOT NULL
            AND coalesce(array_length(last_failure_rules, 1), 0) >= 1)
    );

ALTER TABLE docs.corpus_certification
    DROP CONSTRAINT IF EXISTS corpus_certification_unavailable_not_certified;
ALTER TABLE docs.corpus_certification
    ADD CONSTRAINT corpus_certification_unavailable_not_certified CHECK (
        state <> 'UNAVAILABLE' OR last_certified_at IS NULL OR state_version > 0
    );

ALTER TABLE docs.corpus_certification
    DROP CONSTRAINT IF EXISTS corpus_certification_usable_contract_nonempty;
ALTER TABLE docs.corpus_certification
    ADD CONSTRAINT corpus_certification_usable_contract_nonempty CHECK (
        state IN ('DEPLOYMENT_FAILED', 'UNAVAILABLE')
        OR coalesce(validator_contract_version, '') <> ''
    );

ALTER TABLE docs.corpus_certification
    DROP CONSTRAINT IF EXISTS corpus_certification_certified_implies_version;
ALTER TABLE docs.corpus_certification
    ADD CONSTRAINT corpus_certification_certified_implies_version CHECK (
        last_certified_at IS NULL OR state_version >= 1
    );

ALTER TABLE docs.corpus_certification
    DROP CONSTRAINT IF EXISTS corpus_certification_target_needs_working;
ALTER TABLE docs.corpus_certification
    ADD CONSTRAINT corpus_certification_target_needs_working CHECK (
        deployment_target_snapshot_id IS NULL OR working_corpus_snapshot_id IS NOT NULL
    );
