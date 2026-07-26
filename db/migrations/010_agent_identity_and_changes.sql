-- Agent-first product resources: stable page identities, named principals and change proposals.
--
-- This migration intentionally creates proposal/validation state only. Applying a proposal to authored
-- state remains held behind the WP8 mutation guard and release orchestrator.

ALTER TABLE docs.pages
    ADD COLUMN IF NOT EXISTS resource_id uuid;

UPDATE docs.pages
   SET resource_id = gen_random_uuid()
 WHERE resource_id IS NULL;

ALTER TABLE docs.pages
    ALTER COLUMN resource_id SET DEFAULT gen_random_uuid(),
    ALTER COLUMN resource_id SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS docs_pages_resource_id_uidx
    ON docs.pages (resource_id);

ALTER TABLE docs.page_versions
    ADD COLUMN IF NOT EXISTS resource_id uuid;

UPDATE docs.page_versions v
   SET resource_id = p.resource_id
  FROM docs.pages p
 WHERE v.resource_id IS NULL
   AND v.path = p.path;

CREATE INDEX IF NOT EXISTS docs_page_versions_resource_id_idx
    ON docs.page_versions (resource_id, archived_at DESC);

CREATE TABLE IF NOT EXISTS docplane.principals (
    principal_id       uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_kind     text        NOT NULL,
    display_name       text        NOT NULL,
    owner_principal_id uuid        REFERENCES docplane.principals(principal_id),
    status             text        NOT NULL DEFAULT 'ACTIVE',
    metadata           jsonb       NOT NULL DEFAULT '{}',
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT principals_kind_valid
        CHECK (principal_kind IN ('HUMAN', 'AGENT', 'AUTOMATION')),
    CONSTRAINT principals_status_valid
        CHECK (status IN ('ACTIVE', 'SUSPENDED', 'REVOKED')),
    CONSTRAINT principals_display_name_nonempty
        CHECK (btrim(display_name) <> '')
);

CREATE TABLE IF NOT EXISTS docplane.api_tokens (
    token_id       uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id   uuid        NOT NULL REFERENCES docplane.principals(principal_id),
    token_hash     text        NOT NULL UNIQUE,
    token_prefix   text        NOT NULL,
    scopes         text[]      NOT NULL,
    description    text,
    issued_at      timestamptz NOT NULL DEFAULT now(),
    expires_at     timestamptz,
    last_used_at   timestamptz,
    revoked_at     timestamptz,

    CONSTRAINT api_tokens_hash_sha256
        CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT api_tokens_prefix_bounded
        CHECK (length(token_prefix) BETWEEN 4 AND 24),
    CONSTRAINT api_tokens_scopes_nonempty
        CHECK (coalesce(array_length(scopes, 1), 0) >= 1),
    CONSTRAINT api_tokens_expiry_after_issue
        CHECK (expires_at IS NULL OR expires_at > issued_at)
);

CREATE INDEX IF NOT EXISTS api_tokens_principal_idx
    ON docplane.api_tokens (principal_id)
    WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS docs.change_proposals (
    change_id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_key        text        NOT NULL DEFAULT 'default',
    title                text        NOT NULL,
    purpose              text        NOT NULL,
    author_principal_id  uuid        NOT NULL REFERENCES docplane.principals(principal_id),
    idempotency_key      text        NOT NULL,
    status               text        NOT NULL DEFAULT 'DRAFT',
    base_state_identity  text,
    validation_summary   jsonb,
    preview_receipt      jsonb,
    submitted_at         timestamptz,
    resolved_at          timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT change_proposals_title_nonempty CHECK (btrim(title) <> ''),
    CONSTRAINT change_proposals_purpose_nonempty CHECK (btrim(purpose) <> ''),
    CONSTRAINT change_proposals_workspace_key_valid
        CHECK (workspace_key ~ '^[a-z0-9][a-z0-9_-]{0,62}$'),
    CONSTRAINT change_proposals_idempotency_nonempty CHECK (btrim(idempotency_key) <> ''),
    CONSTRAINT change_proposals_status_valid
        CHECK (status IN ('DRAFT', 'VALIDATED', 'SUBMITTED', 'REJECTED', 'ABANDONED', 'MERGED')),
    CONSTRAINT change_proposals_merged_is_resolved
        CHECK (status <> 'MERGED' OR resolved_at IS NOT NULL),
    UNIQUE (author_principal_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS change_proposals_status_idx
    ON docs.change_proposals (status, updated_at DESC);

CREATE TABLE IF NOT EXISTS docs.change_operations (
    operation_id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    change_id             uuid        NOT NULL REFERENCES docs.change_proposals(change_id) ON DELETE CASCADE,
    sequence              integer     NOT NULL,
    idempotency_key       text        NOT NULL,
    operation_type        text        NOT NULL,
    page_resource_id      uuid,
    expected_revision     text,
    expected_section_hash text,
    payload               jsonb       NOT NULL,
    validation_result     jsonb,
    created_at            timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT change_operations_sequence_nonnegative CHECK (sequence >= 0),
    CONSTRAINT change_operations_idempotency_nonempty CHECK (btrim(idempotency_key) <> ''),
    CONSTRAINT change_operations_type_valid CHECK (
        operation_type IN (
            'PATCH_METADATA',
            'REPLACE_SECTION',
            'INSERT_BEFORE_HEADING',
            'INSERT_AFTER_HEADING',
            'REPLACE_CONTENT_RANGE',
            'ADD_LINK',
            'REMOVE_LINK',
            'CREATE_FROM_TEMPLATE',
            'MOVE_PAGE',
            'REPARENT_NAV',
            'ARCHIVE_PAGE',
            'RESTORE_PAGE',
            'ADD_REDIRECT',
            'REPLACE_DOCUMENT'
        )
    ),
    CONSTRAINT change_operations_existing_page_needs_revision CHECK (
        operation_type IN ('CREATE_FROM_TEMPLATE')
        OR (page_resource_id IS NOT NULL AND expected_revision IS NOT NULL)
    ),
    UNIQUE (change_id, sequence),
    UNIQUE (change_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS change_operations_page_idx
    ON docs.change_operations (page_resource_id);

CREATE OR REPLACE FUNCTION docplane.touch_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS principals_touch_updated_at ON docplane.principals;
CREATE TRIGGER principals_touch_updated_at
    BEFORE UPDATE ON docplane.principals
    FOR EACH ROW EXECUTE FUNCTION docplane.touch_updated_at();

DROP TRIGGER IF EXISTS change_proposals_touch_updated_at ON docs.change_proposals;
CREATE TRIGGER change_proposals_touch_updated_at
    BEFORE UPDATE ON docs.change_proposals
    FOR EACH ROW EXECUTE FUNCTION docplane.touch_updated_at();
