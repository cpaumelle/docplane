-- WP8 — manual-only policy recertification is a distinct deployment mode.

ALTER TABLE docs.deployment_attempts
    DROP CONSTRAINT IF EXISTS deployment_attempts_mode_valid;
ALTER TABLE docs.deployment_attempts
    ADD CONSTRAINT deployment_attempts_mode_valid
        CHECK (mode IN ('CATCH_UP', 'REDEPLOY', 'BOOTSTRAP', 'RECERTIFY_POLICY'));
