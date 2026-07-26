-- WP8 — durable deployment demand and progress.

CREATE TABLE IF NOT EXISTS docs.deployment_attempts (
    deployment_cycle_id  text        PRIMARY KEY,
    mode                 text        NOT NULL,
    status               text        NOT NULL,
    target_page_identity            text  NOT NULL,
    target_state_identity           text  NOT NULL,
    target_state_components         jsonb NOT NULL,
    state_identity_contract_version text  NOT NULL,
    renderer_provenance             jsonb,
    publication_provenance          jsonb,
    validator_contract_version      text,
    release_id                      text,
    failure_rules                   text[] NOT NULL DEFAULT '{}',
    started_at                      timestamptz NOT NULL DEFAULT now(),
    promoted_at                     timestamptz,
    completed_at                    timestamptz,

    CONSTRAINT deployment_attempts_mode_valid
        CHECK (mode IN ('CATCH_UP', 'REDEPLOY', 'BOOTSTRAP')),
    CONSTRAINT deployment_attempts_status_valid
        CHECK (status IN ('PINNED', 'BUILDING', 'VALIDATED', 'PROMOTED',
                          'COMPLETED', 'FAILED', 'RECONCILIATION_REQUIRED')),
    CONSTRAINT deployment_attempts_cycle_is_uuid
        CHECK (deployment_cycle_id ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'),
    CONSTRAINT deployment_attempts_target_names_all_components
        CHECK (target_state_components ?& ARRAY['page_corpus_identity',
                                                'redirect_register_identity',
                                                'navigation_state_identity']),
    CONSTRAINT deployment_attempts_completed_has_release
        CHECK (status <> 'COMPLETED'
               OR (release_id IS NOT NULL AND promoted_at IS NOT NULL
                   AND completed_at IS NOT NULL)),
    CONSTRAINT deployment_attempts_promoted_has_release
        CHECK (status NOT IN ('PROMOTED', 'COMPLETED') OR release_id IS NOT NULL),
    CONSTRAINT deployment_attempts_failed_has_rules
        CHECK (status NOT IN ('FAILED', 'RECONCILIATION_REQUIRED')
               OR coalesce(array_length(failure_rules, 1), 0) >= 1)
);

CREATE UNIQUE INDEX IF NOT EXISTS deployment_attempts_one_active
    ON docs.deployment_attempts ((TRUE))
    WHERE status IN ('PINNED', 'BUILDING', 'VALIDATED', 'PROMOTED',
                     'RECONCILIATION_REQUIRED');

CREATE INDEX IF NOT EXISTS deployment_attempts_started_idx
    ON docs.deployment_attempts (started_at DESC);

ALTER TABLE docs.corpus_certification
    ADD COLUMN IF NOT EXISTS deployment_cycle_id text;
