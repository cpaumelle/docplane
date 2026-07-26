-- WP8 — page identity remains diagnostic; combined documentation state governs trust.

ALTER TABLE docs.corpus_certification
    DROP CONSTRAINT IF EXISTS corpus_certification_current_ids_match;
ALTER TABLE docs.corpus_certification
    ADD CONSTRAINT corpus_certification_current_page_ids_present
    CHECK (state <> 'CURRENT'
           OR (working_corpus_snapshot_id IS NOT NULL
               AND deployed_build_snapshot_id IS NOT NULL));

ALTER TABLE docs.corpus_certification
    DROP CONSTRAINT IF EXISTS corpus_certification_ahead_ids_differ;
ALTER TABLE docs.corpus_certification
    ADD CONSTRAINT corpus_certification_ahead_page_ids_present
    CHECK (state <> 'WORKING_AHEAD'
           OR (working_corpus_snapshot_id IS NOT NULL
               AND deployed_build_snapshot_id IS NOT NULL));
