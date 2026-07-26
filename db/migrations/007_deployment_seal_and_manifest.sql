-- WP8 — bind durable attempts to the exact validated and sealed release.

ALTER TABLE docs.deployment_attempts
    ADD COLUMN IF NOT EXISTS validated_manifest_identity text,
    ADD COLUMN IF NOT EXISTS sealed_digest              text;

ALTER TABLE docs.deployment_attempts
    DROP CONSTRAINT IF EXISTS deployment_attempts_completed_is_sealed;
ALTER TABLE docs.deployment_attempts
    ADD CONSTRAINT deployment_attempts_completed_is_sealed
        CHECK (status <> 'COMPLETED'
               OR (validated_manifest_identity IS NOT NULL AND sealed_digest IS NOT NULL));
