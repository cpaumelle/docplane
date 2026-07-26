-- WP8 — validation policy is part of certification identity.

ALTER TABLE docs.deployment_attempts
    ADD COLUMN IF NOT EXISTS validation_policy_identity   text,
    ADD COLUMN IF NOT EXISTS validation_policy_components jsonb;

ALTER TABLE docs.corpus_certification
    ADD COLUMN IF NOT EXISTS validation_policy_identity   text,
    ADD COLUMN IF NOT EXISTS validation_policy_components jsonb;

ALTER TABLE docs.deployment_attempts
    DROP CONSTRAINT IF EXISTS deployment_attempts_completed_has_policy;
ALTER TABLE docs.deployment_attempts
    ADD CONSTRAINT deployment_attempts_completed_has_policy
        CHECK (status <> 'COMPLETED' OR validation_policy_identity IS NOT NULL);
