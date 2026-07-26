-- Page ownership, classification, verification and maintenance audit.
--
-- Trust metadata is explicit and revision-bound. Content revision changes automatically invalidate a
-- current verification; no client, agent or deployment may continue presenting an old verification as
-- applying to a new revision.

ALTER TABLE docs.pages
    ADD COLUMN IF NOT EXISTS metadata_version bigint NOT NULL DEFAULT 1;

ALTER TABLE docs.pages
    DROP CONSTRAINT IF EXISTS docs_pages_metadata_version_positive;
ALTER TABLE docs.pages
    ADD CONSTRAINT docs_pages_metadata_version_positive CHECK (metadata_version >= 1);

CREATE TABLE IF NOT EXISTS docs.page_metadata_history (
    history_id             bigserial   PRIMARY KEY,
    page_resource_id       uuid        NOT NULL,
    metadata_version       bigint      NOT NULL,
    workspace_id           uuid        NOT NULL,
    publication_state      text        NOT NULL,
    knowledge_class        text,
    verification_state     text        NOT NULL,
    owner_principal_id     uuid,
    review_due_at          timestamptz,
    criticality            text        NOT NULL,
    metadata_review_required boolean   NOT NULL,
    changed_by_principal_id uuid        NOT NULL REFERENCES docplane.principals(principal_id),
    change_reason          text        NOT NULL,
    recorded_at            timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT page_metadata_history_version_positive CHECK (metadata_version >= 1),
    CONSTRAINT page_metadata_history_reason_nonempty CHECK (btrim(change_reason) <> '')
);

CREATE INDEX IF NOT EXISTS page_metadata_history_page_idx
    ON docs.page_metadata_history (page_resource_id, metadata_version DESC);

CREATE TABLE IF NOT EXISTS docs.trust_mutation_receipts (
    principal_id    uuid        NOT NULL REFERENCES docplane.principals(principal_id),
    idempotency_key text        NOT NULL,
    operation_type  text        NOT NULL,
    page_resource_id uuid       NOT NULL,
    request_hash    text        NOT NULL,
    response        jsonb       NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (principal_id, idempotency_key),
    CONSTRAINT trust_receipts_key_nonempty CHECK (btrim(idempotency_key) <> ''),
    CONSTRAINT trust_receipts_operation_nonempty CHECK (btrim(operation_type) <> ''),
    CONSTRAINT trust_receipts_hash_sha256 CHECK (request_hash ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS trust_mutation_receipts_page_idx
    ON docs.trust_mutation_receipts (page_resource_id, created_at DESC);

CREATE OR REPLACE FUNCTION docs.invalidate_verification_on_revision_change()
RETURNS trigger AS $$
BEGIN
    IF NEW.revision IS DISTINCT FROM OLD.revision THEN
        UPDATE docs.page_verifications
           SET verification_state = 'OUTDATED'
         WHERE page_resource_id = NEW.resource_id
           AND verification_state = 'VERIFIED';

        IF OLD.verification_state = 'VERIFIED' THEN
            NEW.verification_state := 'OUTDATED';
            NEW.review_due_at := now();
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS docs_pages_invalidate_verification ON docs.pages;
CREATE TRIGGER docs_pages_invalidate_verification
    BEFORE UPDATE OF revision ON docs.pages
    FOR EACH ROW EXECUTE FUNCTION docs.invalidate_verification_on_revision_change();

CREATE OR REPLACE FUNCTION docs.expire_due_verifications(cutoff timestamptz DEFAULT now())
RETURNS integer AS $$
DECLARE
    changed integer;
BEGIN
    WITH expired AS (
        UPDATE docs.page_verifications
           SET verification_state = 'EXPIRED'
         WHERE verification_state = 'VERIFIED'
           AND expires_at IS NOT NULL
           AND expires_at <= cutoff
        RETURNING page_resource_id
    )
    UPDATE docs.pages p
       SET verification_state = 'EXPIRED',
           review_due_at = coalesce(p.review_due_at, cutoff)
     WHERE p.resource_id IN (SELECT page_resource_id FROM expired)
       AND p.verification_state = 'VERIFIED';
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION docs.expire_due_verifications(timestamptz) IS
    'Idempotently expires revision-bound verifications; invoke through the maintenance API or scheduler.';
