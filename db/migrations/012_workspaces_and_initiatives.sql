-- First-class workspaces, authored-knowledge metadata and active initiatives.
--
-- Publication, semantic class, verification and work state are independent dimensions. Active work is
-- searchable and auditable but is never interpreted as certified durable knowledge by implication.

CREATE SCHEMA IF NOT EXISTS work;

CREATE TABLE IF NOT EXISTS docplane.workspaces (
    workspace_id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_key             text        NOT NULL UNIQUE,
    name                      text        NOT NULL,
    workspace_kind            text        NOT NULL,
    visibility                text        NOT NULL DEFAULT 'INTERNAL',
    mutation_policy           text        NOT NULL DEFAULT 'PROPOSAL_REQUIRED',
    default_verification_days integer,
    retention_policy          jsonb       NOT NULL DEFAULT '{}',
    system_workspace          boolean     NOT NULL DEFAULT FALSE,
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT workspaces_key_valid
        CHECK (workspace_key ~ '^[a-z0-9][a-z0-9_-]{0,62}$'),
    CONSTRAINT workspaces_name_nonempty CHECK (btrim(name) <> ''),
    CONSTRAINT workspaces_kind_valid
        CHECK (workspace_kind IN ('REFERENCE', 'OPERATIONS', 'WORK', 'MIGRATION')),
    CONSTRAINT workspaces_visibility_valid
        CHECK (visibility IN ('PRIVATE', 'INTERNAL', 'PUBLIC')),
    CONSTRAINT workspaces_mutation_policy_valid
        CHECK (mutation_policy IN ('PROPOSAL_REQUIRED', 'DIRECT_LOW_RISK', 'DIRECT_ALLOWED')),
    CONSTRAINT workspaces_verification_days_positive
        CHECK (default_verification_days IS NULL OR default_verification_days >= 1)
);

INSERT INTO docplane.workspaces
    (workspace_key, name, workspace_kind, mutation_policy, default_verification_days, system_workspace)
VALUES
    ('reference', 'Reference', 'REFERENCE', 'PROPOSAL_REQUIRED', 180, TRUE),
    ('operations', 'Operations', 'OPERATIONS', 'PROPOSAL_REQUIRED', 90, TRUE),
    ('work', 'Active Work', 'WORK', 'DIRECT_LOW_RISK', NULL, TRUE),
    ('migration-import', 'Migration Import', 'MIGRATION', 'PROPOSAL_REQUIRED', NULL, TRUE)
ON CONFLICT (workspace_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS docplane.workspace_memberships (
    workspace_id uuid        NOT NULL REFERENCES docplane.workspaces(workspace_id) ON DELETE CASCADE,
    principal_id uuid        NOT NULL REFERENCES docplane.principals(principal_id) ON DELETE CASCADE,
    role         text        NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid        REFERENCES docplane.principals(principal_id),

    PRIMARY KEY (workspace_id, principal_id),
    CONSTRAINT workspace_memberships_role_valid
        CHECK (role IN ('READER', 'PROPOSER', 'EDITOR', 'REVIEWER', 'MERGER', 'OWNER'))
);

ALTER TABLE docs.pages
    ADD COLUMN IF NOT EXISTS workspace_id       uuid REFERENCES docplane.workspaces(workspace_id),
    ADD COLUMN IF NOT EXISTS publication_state text,
    ADD COLUMN IF NOT EXISTS knowledge_class   text,
    ADD COLUMN IF NOT EXISTS verification_state text NOT NULL DEFAULT 'UNVERIFIED',
    ADD COLUMN IF NOT EXISTS owner_principal_id uuid REFERENCES docplane.principals(principal_id),
    ADD COLUMN IF NOT EXISTS review_due_at      timestamptz,
    ADD COLUMN IF NOT EXISTS criticality        text NOT NULL DEFAULT 'NORMAL',
    ADD COLUMN IF NOT EXISTS metadata_review_required boolean NOT NULL DEFAULT TRUE;

UPDATE docs.pages
   SET workspace_id = (SELECT workspace_id FROM docplane.workspaces WHERE workspace_key = 'migration-import')
 WHERE workspace_id IS NULL;

UPDATE docs.pages
   SET publication_state = CASE status
       WHEN 'active' THEN 'PUBLISHED'
       WHEN 'archived' THEN 'ARCHIVED'
       ELSE 'DRAFT'
   END
 WHERE publication_state IS NULL;

ALTER TABLE docs.pages
    ALTER COLUMN workspace_id SET NOT NULL,
    ALTER COLUMN publication_state SET NOT NULL;

ALTER TABLE docs.pages
    DROP CONSTRAINT IF EXISTS docs_pages_publication_state_valid;
ALTER TABLE docs.pages
    ADD CONSTRAINT docs_pages_publication_state_valid
        CHECK (publication_state IN ('DRAFT', 'PUBLISHED', 'ARCHIVED'));

ALTER TABLE docs.pages
    DROP CONSTRAINT IF EXISTS docs_pages_knowledge_class_valid;
ALTER TABLE docs.pages
    ADD CONSTRAINT docs_pages_knowledge_class_valid
        CHECK (knowledge_class IS NULL OR knowledge_class IN (
            'REFERENCE', 'OPERATION', 'ARCHITECTURE', 'DESIGN',
            'DECISION', 'POLICY', 'EVIDENCE', 'WORK_NOTE'
        ));

ALTER TABLE docs.pages
    DROP CONSTRAINT IF EXISTS docs_pages_verification_state_valid;
ALTER TABLE docs.pages
    ADD CONSTRAINT docs_pages_verification_state_valid
        CHECK (verification_state IN ('UNVERIFIED', 'VERIFIED', 'EXPIRED', 'OUTDATED'));

ALTER TABLE docs.pages
    DROP CONSTRAINT IF EXISTS docs_pages_criticality_valid;
ALTER TABLE docs.pages
    ADD CONSTRAINT docs_pages_criticality_valid
        CHECK (criticality IN ('NORMAL', 'IMPORTANT', 'OPERATIONAL_CRITICAL', 'POLICY_REQUIRED'));

CREATE INDEX IF NOT EXISTS docs_pages_workspace_idx
    ON docs.pages (workspace_id, publication_state, knowledge_class);
CREATE INDEX IF NOT EXISTS docs_pages_verification_due_idx
    ON docs.pages (verification_state, review_due_at)
    WHERE publication_state = 'PUBLISHED';
CREATE INDEX IF NOT EXISTS docs_pages_owner_idx
    ON docs.pages (owner_principal_id)
    WHERE owner_principal_id IS NOT NULL;

CREATE OR REPLACE FUNCTION docplane.default_page_workspace()
RETURNS trigger AS $$
BEGIN
    IF NEW.workspace_id IS NULL THEN
        SELECT workspace_id INTO NEW.workspace_id
          FROM docplane.workspaces
         WHERE workspace_key = 'reference';
    END IF;
    IF NEW.publication_state IS NULL THEN
        NEW.publication_state := CASE NEW.status
            WHEN 'active' THEN 'PUBLISHED'
            WHEN 'archived' THEN 'ARCHIVED'
            ELSE 'DRAFT'
        END;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS docs_pages_default_workspace ON docs.pages;
CREATE TRIGGER docs_pages_default_workspace
    BEFORE INSERT ON docs.pages
    FOR EACH ROW EXECUTE FUNCTION docplane.default_page_workspace();

CREATE TABLE IF NOT EXISTS docs.page_verifications (
    verification_id     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    page_resource_id    uuid        NOT NULL,
    page_revision       text        NOT NULL,
    verifier_principal_id uuid      NOT NULL REFERENCES docplane.principals(principal_id),
    verification_state  text        NOT NULL,
    verified_at         timestamptz NOT NULL DEFAULT now(),
    expires_at          timestamptz,
    notes               text,
    created_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT page_verifications_state_valid
        CHECK (verification_state IN ('VERIFIED', 'EXPIRED', 'OUTDATED', 'REVOKED')),
    CONSTRAINT page_verifications_expiry_after_verification
        CHECK (expires_at IS NULL OR expires_at > verified_at)
);

CREATE INDEX IF NOT EXISTS page_verifications_page_idx
    ON docs.page_verifications (page_resource_id, verified_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS page_verifications_one_current
    ON docs.page_verifications (page_resource_id)
    WHERE verification_state = 'VERIFIED';

CREATE TABLE IF NOT EXISTS work.initiatives (
    initiative_id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    initiative_key         text        NOT NULL,
    workspace_id           uuid        NOT NULL REFERENCES docplane.workspaces(workspace_id),
    title                  text        NOT NULL,
    objective              text        NOT NULL,
    scope                  text,
    owner_principal_id     uuid        NOT NULL REFERENCES docplane.principals(principal_id),
    idempotency_key        text        NOT NULL,
    work_state             text        NOT NULL DEFAULT 'BACKLOG',
    priority               text        NOT NULL DEFAULT 'NORMAL',
    version                bigint      NOT NULL DEFAULT 1,
    target_date            date,
    review_due_at          timestamptz,
    blocker_summary        text,
    soak_started_at        timestamptz,
    soak_review_at         timestamptz,
    soak_success_criteria  text,
    soak_failure_conditions text,
    parked_reason          text,
    parked_review_at       timestamptz,
    parked_indefinitely    boolean     NOT NULL DEFAULT FALSE,
    promotion_state        text        NOT NULL DEFAULT 'NOT_READY',
    promotion_change_id    uuid        REFERENCES docs.change_proposals(change_id),
    completed_at           timestamptz,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT initiatives_key_valid
        CHECK (initiative_key ~ '^[a-z0-9][a-z0-9_-]{0,95}$'),
    CONSTRAINT initiatives_title_nonempty CHECK (btrim(title) <> ''),
    CONSTRAINT initiatives_objective_nonempty CHECK (btrim(objective) <> ''),
    CONSTRAINT initiatives_idempotency_nonempty CHECK (btrim(idempotency_key) <> ''),
    CONSTRAINT initiatives_work_state_valid
        CHECK (work_state IN ('BACKLOG', 'ACTIVE', 'BLOCKED', 'SOAKING', 'PAUSED',
                              'PARKED', 'COMPLETE', 'ABANDONED')),
    CONSTRAINT initiatives_priority_valid
        CHECK (priority IN ('LOW', 'NORMAL', 'HIGH', 'CRITICAL')),
    CONSTRAINT initiatives_promotion_state_valid
        CHECK (promotion_state IN ('NOT_READY', 'READY', 'IN_PROGRESS', 'PROMOTED', 'NOT_REQUIRED')),
    CONSTRAINT initiatives_version_positive CHECK (version >= 1),
    CONSTRAINT initiatives_soaking_contract CHECK (
        work_state <> 'SOAKING'
        OR (soak_started_at IS NOT NULL
            AND soak_review_at IS NOT NULL
            AND btrim(coalesce(soak_success_criteria, '')) <> ''
            AND btrim(coalesce(soak_failure_conditions, '')) <> '')
    ),
    CONSTRAINT initiatives_parked_contract CHECK (
        work_state <> 'PARKED'
        OR (btrim(coalesce(parked_reason, '')) <> ''
            AND (parked_review_at IS NOT NULL OR parked_indefinitely))
    ),
    CONSTRAINT initiatives_complete_timestamp CHECK (
        work_state NOT IN ('COMPLETE', 'ABANDONED') OR completed_at IS NOT NULL
    ),
    CONSTRAINT initiatives_promoted_has_change CHECK (
        promotion_state <> 'PROMOTED' OR promotion_change_id IS NOT NULL
    ),
    UNIQUE (workspace_id, initiative_key),
    UNIQUE (owner_principal_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS initiatives_state_idx
    ON work.initiatives (work_state, review_due_at, updated_at DESC);
CREATE INDEX IF NOT EXISTS initiatives_owner_idx
    ON work.initiatives (owner_principal_id, work_state);
CREATE INDEX IF NOT EXISTS initiatives_soak_due_idx
    ON work.initiatives (soak_review_at)
    WHERE work_state = 'SOAKING';
CREATE INDEX IF NOT EXISTS initiatives_parked_due_idx
    ON work.initiatives (parked_review_at)
    WHERE work_state = 'PARKED' AND parked_indefinitely = FALSE;

CREATE OR REPLACE FUNCTION work.require_work_workspace()
RETURNS trigger AS $$
DECLARE
    kind text;
BEGIN
    SELECT workspace_kind INTO kind
      FROM docplane.workspaces
     WHERE workspace_id = NEW.workspace_id;
    IF kind IS DISTINCT FROM 'WORK' THEN
        RAISE EXCEPTION 'initiative workspace must have kind WORK (got %)', kind
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS initiatives_require_work_workspace ON work.initiatives;
CREATE TRIGGER initiatives_require_work_workspace
    BEFORE INSERT OR UPDATE ON work.initiatives
    FOR EACH ROW EXECUTE FUNCTION work.require_work_workspace();

CREATE TABLE IF NOT EXISTS work.initiative_activities (
    activity_id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    initiative_id       uuid        NOT NULL REFERENCES work.initiatives(initiative_id) ON DELETE CASCADE,
    author_principal_id uuid        NOT NULL REFERENCES docplane.principals(principal_id),
    idempotency_key     text        NOT NULL,
    activity_type       text        NOT NULL,
    body                text        NOT NULL,
    metadata            jsonb       NOT NULL DEFAULT '{}',
    created_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT initiative_activities_type_valid
        CHECK (activity_type IN ('NOTE', 'OBSERVATION', 'DECISION_REQUIRED', 'HANDOFF',
                                 'SOAK_OBSERVATION', 'BLOCKER', 'RESOLUTION')),
    CONSTRAINT initiative_activities_body_nonempty CHECK (btrim(body) <> ''),
    CONSTRAINT initiative_activities_idempotency_nonempty CHECK (btrim(idempotency_key) <> ''),
    UNIQUE (initiative_id, author_principal_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS initiative_activities_timeline_idx
    ON work.initiative_activities (initiative_id, created_at DESC);

CREATE TABLE IF NOT EXISTS work.initiative_links (
    initiative_id      uuid        NOT NULL REFERENCES work.initiatives(initiative_id) ON DELETE CASCADE,
    relation           text        NOT NULL,
    resource_type      text        NOT NULL,
    resource_id        text        NOT NULL,
    created_by         uuid        NOT NULL REFERENCES docplane.principals(principal_id),
    created_at         timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (initiative_id, relation, resource_type, resource_id),
    CONSTRAINT initiative_links_relation_valid
        CHECK (relation IN ('CONTEXT', 'EVIDENCE', 'DECISION', 'RUNBOOK', 'BLOCKED_BY',
                            'PROMOTES_TO', 'CHANGE_PROPOSAL', 'CATALOG_SNAPSHOT')),
    CONSTRAINT initiative_links_resource_type_valid
        CHECK (resource_type IN ('PAGE', 'INITIATIVE', 'CHANGE', 'CATALOG', 'EXTERNAL')),
    CONSTRAINT initiative_links_resource_nonempty CHECK (btrim(resource_id) <> '')
);

CREATE TABLE IF NOT EXISTS work.initiative_dependencies (
    initiative_id           uuid NOT NULL REFERENCES work.initiatives(initiative_id) ON DELETE CASCADE,
    depends_on_initiative_id uuid NOT NULL REFERENCES work.initiatives(initiative_id) ON DELETE CASCADE,
    dependency_kind         text NOT NULL DEFAULT 'REQUIRES',
    created_at              timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (initiative_id, depends_on_initiative_id),
    CONSTRAINT initiative_dependencies_no_self CHECK (initiative_id <> depends_on_initiative_id),
    CONSTRAINT initiative_dependencies_kind_valid
        CHECK (dependency_kind IN ('REQUIRES', 'BLOCKED_BY', 'RELATED'))
);

COMMENT ON TABLE work.initiatives IS
    'First-class active work; never treated as durable certified knowledge without explicit promotion.';
COMMENT ON COLUMN docs.pages.metadata_review_required IS
    'True until imported legacy metadata is classified and reviewed in the new orthogonal model.';
