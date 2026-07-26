-- WP8 — extend certification from page identity to complete documentation-state identity.

ALTER TABLE docs.corpus_certification
    ADD COLUMN IF NOT EXISTS working_state_snapshot_id            text,
    ADD COLUMN IF NOT EXISTS deployed_state_snapshot_id           text,
    ADD COLUMN IF NOT EXISTS deployment_target_state_snapshot_id  text,
    ADD COLUMN IF NOT EXISTS working_state_components             jsonb,
    ADD COLUMN IF NOT EXISTS deployed_state_components            jsonb,
    ADD COLUMN IF NOT EXISTS deployment_target_state_components   jsonb,
    ADD COLUMN IF NOT EXISTS state_identity_contract_version      text;

-- A page-only certification cannot prove redirect or navigation state. Existing rows are
-- demoted rather than reinterpreted as stronger evidence.
UPDATE docs.corpus_certification
   SET state = 'UNAVAILABLE',
       last_failure_rules = ARRAY['LEGACY_PAGE_ONLY_IDENTITY_INSUFFICIENT'],
       state_version = state_version + 1
 WHERE id = TRUE
   AND working_state_snapshot_id IS NULL
   AND state IN ('CURRENT', 'WORKING_AHEAD');

ALTER TABLE docs.corpus_certification
    DROP CONSTRAINT IF EXISTS corpus_certification_current_state_ids_match;
ALTER TABLE docs.corpus_certification
    ADD CONSTRAINT corpus_certification_current_state_ids_match
    CHECK (state <> 'CURRENT'
           OR (working_state_snapshot_id IS NOT NULL
               AND deployed_state_snapshot_id IS NOT NULL
               AND working_state_snapshot_id = deployed_state_snapshot_id
               AND deployment_target_state_snapshot_id IS NULL));

ALTER TABLE docs.corpus_certification
    DROP CONSTRAINT IF EXISTS corpus_certification_ahead_state_ids_differ;
ALTER TABLE docs.corpus_certification
    ADD CONSTRAINT corpus_certification_ahead_state_ids_differ
    CHECK (state <> 'WORKING_AHEAD'
           OR (working_state_snapshot_id IS NOT NULL
               AND deployed_state_snapshot_id IS NOT NULL
               AND working_state_snapshot_id <> deployed_state_snapshot_id));

ALTER TABLE docs.corpus_certification
    DROP CONSTRAINT IF EXISTS corpus_certification_usable_needs_state_identity;
ALTER TABLE docs.corpus_certification
    ADD CONSTRAINT corpus_certification_usable_needs_state_identity
    CHECK (state IN ('DEPLOYMENT_FAILED', 'UNAVAILABLE')
           OR (working_state_snapshot_id IS NOT NULL
               AND deployed_state_snapshot_id IS NOT NULL
               AND state_identity_contract_version IS NOT NULL
               AND working_state_components IS NOT NULL
               AND deployed_state_components IS NOT NULL));

ALTER TABLE docs.corpus_certification
    DROP CONSTRAINT IF EXISTS corpus_certification_pin_names_all_components;
ALTER TABLE docs.corpus_certification
    ADD CONSTRAINT corpus_certification_pin_names_all_components
    CHECK (deployment_target_state_snapshot_id IS NULL
           OR (deployment_target_state_components IS NOT NULL
               AND deployment_target_state_components ?& ARRAY['page_corpus_identity',
                                                               'redirect_register_identity',
                                                               'navigation_state_identity']));

ALTER TABLE docs.corpus_certification
    DROP CONSTRAINT IF EXISTS corpus_certification_components_complete;
ALTER TABLE docs.corpus_certification
    ADD CONSTRAINT corpus_certification_components_complete
    CHECK (state IN ('DEPLOYMENT_FAILED', 'UNAVAILABLE')
           OR (working_state_components ?& ARRAY['page_corpus_identity',
                                                 'redirect_register_identity',
                                                 'navigation_state_identity']
               AND deployed_state_components ?& ARRAY['page_corpus_identity',
                                                      'redirect_register_identity',
                                                      'navigation_state_identity']));
