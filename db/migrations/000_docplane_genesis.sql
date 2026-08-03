CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS docplane;
CREATE SCHEMA IF NOT EXISTS docs;
CREATE SCHEMA IF NOT EXISTS work;

CREATE OR REPLACE FUNCTION docplane.touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE TABLE docplane.principals (
    principal_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_kind text NOT NULL CHECK (principal_kind IN ('HUMAN', 'AGENT', 'AUTOMATION')),
    display_name text NOT NULL CHECK (btrim(display_name) <> ''),
    status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SUSPENDED', 'REVOKED')),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER principals_touch_updated_at
BEFORE UPDATE ON docplane.principals
FOR EACH ROW EXECUTE FUNCTION docplane.touch_updated_at();

CREATE TABLE docplane.api_tokens (
    token_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id uuid NOT NULL REFERENCES docplane.principals(principal_id) ON DELETE CASCADE,
    token_hash text NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    token_prefix text NOT NULL CHECK (length(token_prefix) BETWEEN 4 AND 24),
    description text,
    issued_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    last_used_at timestamptz,
    revoked_at timestamptz,
    CHECK (expires_at IS NULL OR expires_at > issued_at)
);
CREATE INDEX api_tokens_active_principal_idx
    ON docplane.api_tokens(principal_id) WHERE revoked_at IS NULL;

CREATE TABLE docplane.workspaces (
    workspace_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_key text NOT NULL UNIQUE CHECK (workspace_key ~ '^[a-z0-9][a-z0-9_-]{0,62}$'),
    name text NOT NULL CHECK (btrim(name) <> ''),
    workspace_kind text NOT NULL CHECK (workspace_kind IN ('REFERENCE', 'OPERATIONS', 'WORK')),
    visibility text NOT NULL DEFAULT 'INTERNAL' CHECK (visibility IN ('PRIVATE', 'INTERNAL', 'PUBLIC')),
    default_verification_days integer CHECK (default_verification_days IS NULL OR default_verification_days >= 1),
    retention_policy jsonb NOT NULL DEFAULT '{}'::jsonb,
    system_workspace boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER workspaces_touch_updated_at
BEFORE UPDATE ON docplane.workspaces
FOR EACH ROW EXECUTE FUNCTION docplane.touch_updated_at();

INSERT INTO docplane.workspaces
    (workspace_key, name, workspace_kind, visibility, system_workspace)
VALUES
    ('reference', 'Reference', 'REFERENCE', 'INTERNAL', true),
    ('operations', 'Operations', 'OPERATIONS', 'INTERNAL', true),
    ('work', 'Active work', 'WORK', 'INTERNAL', true)
ON CONFLICT (workspace_key) DO NOTHING;

CREATE TABLE docplane.events (
    event_seq bigserial PRIMARY KEY,
    event_id uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    event_type text NOT NULL CHECK (event_type ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    principal_id uuid REFERENCES docplane.principals(principal_id),
    actor_class text NOT NULL CHECK (actor_class IN ('HUMAN', 'AGENT', 'AUTOMATION', 'ANONYMOUS')),
    channel text NOT NULL CHECK (channel IN ('WEB', 'API', 'MCP', 'SEARCH', 'AI', 'CLI', 'WEBHOOK', 'SYSTEM')),
    producer_id text NOT NULL CHECK (btrim(producer_id) <> ''),
    client_identity text,
    workspace_key text CHECK (workspace_key IS NULL OR workspace_key ~ '^[a-z0-9][a-z0-9_-]{0,62}$'),
    resource_type text,
    resource_id text,
    page_resource_id uuid,
    page_path text,
    page_revision text,
    release_id text,
    idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (octet_length(metadata::text) <= 16384),
    ingested_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (producer_id, idempotency_key)
);
CREATE INDEX events_occurred_idx ON docplane.events(occurred_at DESC, event_seq DESC);
CREATE INDEX events_resource_idx ON docplane.events(resource_type, resource_id, event_seq DESC);

CREATE TABLE docs.sections (
    name text PRIMARY KEY CHECK (name = btrim(name) AND name <> '' AND name <> 'Home' AND name !~ '/'),
    sort_order integer NOT NULL CHECK (sort_order >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text NOT NULL
);

CREATE TABLE docs.pages (
    resource_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    path text NOT NULL UNIQUE CHECK (path ~ '^[a-z0-9/_-]+\.md$'),
    title text NOT NULL CHECK (btrim(title) <> ''),
    nav_path text NOT NULL CHECK (btrim(nav_path) <> ''),
    content text NOT NULL CHECK (btrim(content) <> ''),
    revision text NOT NULL DEFAULT gen_random_uuid()::text,
    version bigint NOT NULL DEFAULT 1 CHECK (version >= 1),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    workspace_id uuid NOT NULL REFERENCES docplane.workspaces(workspace_id),
    publication_state text NOT NULL DEFAULT 'PUBLISHED' CHECK (publication_state IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
    knowledge_class text,
    verification_state text NOT NULL DEFAULT 'UNVERIFIED' CHECK (verification_state IN ('UNVERIFIED', 'VERIFIED', 'OUTDATED', 'EXPIRED')),
    owner_principal_id uuid REFERENCES docplane.principals(principal_id),
    review_due_at timestamptz,
    criticality text NOT NULL DEFAULT 'NORMAL' CHECK (criticality IN ('NORMAL', 'IMPORTANT', 'OPERATIONAL_CRITICAL', 'POLICY_REQUIRED')),
    metadata_review_required boolean NOT NULL DEFAULT false,
    metadata_version bigint NOT NULL DEFAULT 1 CHECK (metadata_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text NOT NULL
);
CREATE INDEX pages_status_path_idx ON docs.pages(status, path);
CREATE INDEX pages_workspace_idx ON docs.pages(workspace_id, status);
CREATE INDEX pages_review_due_idx ON docs.pages(review_due_at) WHERE review_due_at IS NOT NULL;

CREATE TABLE docs.changes (
    change_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_key text NOT NULL REFERENCES docplane.workspaces(workspace_key),
    title text NOT NULL CHECK (btrim(title) <> ''),
    purpose text NOT NULL CHECK (btrim(purpose) <> ''),
    author_principal_id uuid NOT NULL REFERENCES docplane.principals(principal_id),
    idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
    status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'VALIDATED', 'PUBLISHED', 'FAILED', 'ABANDONED')),
    base_state_identity text,
    validation_summary jsonb,
    preview_receipt jsonb,
    publication_receipt jsonb,
    failure_receipt jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    UNIQUE (author_principal_id, idempotency_key),
    CHECK (status <> 'PUBLISHED' OR published_at IS NOT NULL)
);
CREATE INDEX changes_status_updated_idx ON docs.changes(status, updated_at DESC);

CREATE TABLE docs.change_operations (
    operation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    change_id uuid NOT NULL REFERENCES docs.changes(change_id) ON DELETE CASCADE,
    sequence integer NOT NULL CHECK (sequence >= 0),
    idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
    operation_type text NOT NULL CHECK (operation_type IN (
        'CREATE_PAGE', 'REPLACE_DOCUMENT', 'PATCH_TEXT', 'PATCH_METADATA',
        'REPLACE_SECTION', 'INSERT_BEFORE_HEADING', 'INSERT_AFTER_HEADING',
        'MOVE_PAGE', 'REPARENT_NAV', 'ARCHIVE_PAGE', 'RESTORE_PAGE',
        'ADD_REDIRECT', 'REMOVE_REDIRECT', 'REORDER_SECTIONS'
    )),
    page_resource_id uuid REFERENCES docs.pages(resource_id),
    expected_revision text,
    expected_section_hash text,
    payload jsonb NOT NULL,
    validation_result jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (change_id, sequence),
    UNIQUE (change_id, idempotency_key),
    CHECK (
        operation_type IN ('CREATE_PAGE', 'ADD_REDIRECT', 'REMOVE_REDIRECT', 'REORDER_SECTIONS')
        OR (page_resource_id IS NOT NULL AND expected_revision IS NOT NULL)
    )
);

CREATE TABLE docs.page_versions (
    version_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    resource_id uuid NOT NULL,
    path text NOT NULL,
    title text NOT NULL,
    nav_path text NOT NULL,
    content text NOT NULL,
    revision text NOT NULL,
    version bigint NOT NULL,
    status text NOT NULL,
    workspace_id uuid NOT NULL REFERENCES docplane.workspaces(workspace_id),
    publication_state text NOT NULL,
    knowledge_class text,
    verification_state text NOT NULL,
    owner_principal_id uuid REFERENCES docplane.principals(principal_id),
    review_due_at timestamptz,
    criticality text NOT NULL,
    metadata_review_required boolean NOT NULL,
    metadata_version bigint NOT NULL,
    updated_by text NOT NULL,
    change_id uuid REFERENCES docs.changes(change_id),
    archived_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (resource_id, revision)
);
CREATE INDEX page_versions_resource_idx ON docs.page_versions(resource_id, archived_at DESC);

CREATE TABLE docs.redirects (
    from_path text PRIMARY KEY CHECK (from_path ~ '^[a-z0-9/_-]+\.md$'),
    to_path text NOT NULL CHECK (to_path ~ '^[a-z0-9/_-]+\.md$' AND to_path <> from_path),
    revision text NOT NULL DEFAULT gen_random_uuid()::text,
    version bigint NOT NULL DEFAULT 1 CHECK (version >= 1),
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text NOT NULL
);

CREATE TABLE docs.deployment_attempts (
    deployment_cycle_id uuid PRIMARY KEY,
    change_id uuid REFERENCES docs.changes(change_id),
    requested_by uuid NOT NULL REFERENCES docplane.principals(principal_id),
    status text NOT NULL CHECK (status IN ('BUILDING', 'COMPLETED', 'FAILED')),
    target_page_identity text NOT NULL,
    target_state_identity text NOT NULL,
    release_id text,
    sealed_digest text,
    receipt jsonb,
    failure jsonb,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CHECK (status <> 'COMPLETED' OR (release_id IS NOT NULL AND sealed_digest IS NOT NULL AND completed_at IS NOT NULL)),
    CHECK (status <> 'FAILED' OR (failure IS NOT NULL AND completed_at IS NOT NULL))
);
CREATE INDEX deployment_attempts_started_idx ON docs.deployment_attempts(started_at DESC);

CREATE TABLE docs.corpus_certification (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    state text NOT NULL DEFAULT 'UNAVAILABLE' CHECK (state IN ('UNAVAILABLE', 'CURRENT', 'DEPLOYMENT_FAILED')),
    working_state_identity text,
    deployed_state_identity text,
    deployment_cycle_id uuid REFERENCES docs.deployment_attempts(deployment_cycle_id),
    release_id text,
    last_failure jsonb,
    state_version bigint NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    last_certified_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO docs.corpus_certification(singleton) VALUES (true) ON CONFLICT DO NOTHING;

CREATE TABLE docs.reorganisation_plans (
    plan_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_key text NOT NULL REFERENCES docplane.workspaces(workspace_key),
    title text NOT NULL CHECK (btrim(title) <> ''),
    purpose text NOT NULL CHECK (btrim(purpose) <> ''),
    author_principal_id uuid NOT NULL REFERENCES docplane.principals(principal_id),
    status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'ANALYZED', 'VALIDATED', 'PUBLISHED', 'FAILED', 'ABANDONED')),
    version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
    base_state_identity text,
    analysis_receipt jsonb,
    validation_receipt jsonb,
    linked_change_id uuid REFERENCES docs.changes(change_id),
    publication_receipt jsonb,
    failure_receipt jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    UNIQUE (author_principal_id, idempotency_key),
    CHECK (status <> 'PUBLISHED' OR published_at IS NOT NULL)
);

CREATE TABLE docs.reorganisation_operations (
    operation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id uuid NOT NULL REFERENCES docs.reorganisation_plans(plan_id) ON DELETE CASCADE,
    sequence integer NOT NULL CHECK (sequence >= 0),
    idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
    operation_type text NOT NULL CHECK (operation_type IN (
        'MOVE_PAGE', 'REPARENT_NAV', 'REORDER_SECTIONS',
        'ADD_REDIRECT', 'REMOVE_REDIRECT', 'ARCHIVE_PAGE', 'RESTORE_PAGE'
    )),
    page_resource_id uuid REFERENCES docs.pages(resource_id),
    expected_revision text,
    payload jsonb NOT NULL,
    analysis_result jsonb,
    inverse_operation jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (plan_id, sequence),
    UNIQUE (plan_id, idempotency_key),
    CHECK (
        operation_type IN ('REORDER_SECTIONS', 'ADD_REDIRECT', 'REMOVE_REDIRECT')
        OR (page_resource_id IS NOT NULL AND expected_revision IS NOT NULL)
    )
);

CREATE TABLE docs.page_verifications (
    verification_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    page_resource_id uuid NOT NULL REFERENCES docs.pages(resource_id) ON DELETE CASCADE,
    page_revision text NOT NULL,
    verification_state text NOT NULL CHECK (verification_state IN ('VERIFIED', 'REVOKED', 'OUTDATED', 'EXPIRED')),
    verifier_principal_id uuid NOT NULL REFERENCES docplane.principals(principal_id),
    verified_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    notes text,
    CHECK (expires_at IS NULL OR expires_at > verified_at)
);
CREATE INDEX page_verifications_page_idx ON docs.page_verifications(page_resource_id, verified_at DESC);

CREATE TABLE docs.page_metadata_history (
    history_id bigserial PRIMARY KEY,
    page_resource_id uuid NOT NULL REFERENCES docs.pages(resource_id) ON DELETE CASCADE,
    metadata_version bigint NOT NULL CHECK (metadata_version >= 1),
    workspace_id uuid NOT NULL REFERENCES docplane.workspaces(workspace_id),
    publication_state text NOT NULL,
    knowledge_class text,
    verification_state text NOT NULL,
    owner_principal_id uuid REFERENCES docplane.principals(principal_id),
    review_due_at timestamptz,
    criticality text NOT NULL,
    metadata_review_required boolean NOT NULL,
    changed_by_principal_id uuid NOT NULL REFERENCES docplane.principals(principal_id),
    change_reason text NOT NULL CHECK (btrim(change_reason) <> ''),
    recorded_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE docs.trust_mutation_receipts (
    principal_id uuid NOT NULL REFERENCES docplane.principals(principal_id),
    idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
    operation_type text NOT NULL CHECK (btrim(operation_type) <> ''),
    page_resource_id uuid NOT NULL REFERENCES docs.pages(resource_id),
    request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    response jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (principal_id, idempotency_key)
);

CREATE OR REPLACE FUNCTION docs.expire_due_verifications(cutoff timestamptz DEFAULT now())
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE changed integer;
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
       SET verification_state = 'EXPIRED', review_due_at = coalesce(p.review_due_at, cutoff)
     WHERE p.resource_id IN (SELECT page_resource_id FROM expired)
       AND p.verification_state = 'VERIFIED';
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed;
END;
$$;

CREATE TABLE work.initiatives (
    initiative_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    initiative_key text NOT NULL CHECK (initiative_key ~ '^[a-z0-9][a-z0-9_-]{0,95}$'),
    workspace_id uuid NOT NULL REFERENCES docplane.workspaces(workspace_id),
    title text NOT NULL CHECK (btrim(title) <> ''),
    objective text NOT NULL CHECK (btrim(objective) <> ''),
    scope text,
    owner_principal_id uuid NOT NULL REFERENCES docplane.principals(principal_id),
    idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
    work_state text NOT NULL DEFAULT 'BACKLOG' CHECK (work_state IN ('BACKLOG', 'ACTIVE', 'BLOCKED', 'SOAKING', 'PAUSED', 'PARKED', 'COMPLETE', 'ABANDONED')),
    priority text NOT NULL DEFAULT 'NORMAL' CHECK (priority IN ('LOW', 'NORMAL', 'HIGH', 'CRITICAL')),
    version bigint NOT NULL DEFAULT 1 CHECK (version >= 1),
    target_date date,
    review_due_at timestamptz,
    blocker_summary text,
    soak_started_at timestamptz,
    soak_review_at timestamptz,
    soak_success_criteria text,
    soak_failure_conditions text,
    parked_reason text,
    parked_review_at timestamptz,
    parked_indefinitely boolean NOT NULL DEFAULT false,
    promotion_state text NOT NULL DEFAULT 'NOT_READY' CHECK (promotion_state IN ('NOT_READY', 'READY', 'IN_PROGRESS', 'PROMOTED', 'NOT_REQUIRED')),
    promotion_change_id uuid REFERENCES docs.changes(change_id),
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, initiative_key),
    UNIQUE (owner_principal_id, idempotency_key),
    CHECK (promotion_state <> 'PROMOTED' OR promotion_change_id IS NOT NULL),
    CHECK (work_state NOT IN ('COMPLETE', 'ABANDONED') OR completed_at IS NOT NULL)
);
CREATE INDEX initiatives_state_updated_idx ON work.initiatives(work_state, updated_at DESC);

CREATE TABLE work.initiative_activities (
    activity_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    initiative_id uuid NOT NULL REFERENCES work.initiatives(initiative_id) ON DELETE CASCADE,
    author_principal_id uuid NOT NULL REFERENCES docplane.principals(principal_id),
    idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
    activity_type text NOT NULL CHECK (activity_type IN ('NOTE', 'OBSERVATION', 'DECISION_REQUIRED', 'HANDOFF', 'SOAK_OBSERVATION', 'BLOCKER', 'RESOLUTION')),
    body text NOT NULL CHECK (btrim(body) <> ''),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (initiative_id, author_principal_id, idempotency_key)
);

CREATE TABLE work.initiative_links (
    initiative_id uuid NOT NULL REFERENCES work.initiatives(initiative_id) ON DELETE CASCADE,
    relation text NOT NULL CHECK (relation IN ('CONTEXT', 'EVIDENCE', 'DECISION', 'RUNBOOK', 'BLOCKED_BY', 'PROMOTES_TO', 'CHANGE', 'CATALOG_SNAPSHOT')),
    resource_type text NOT NULL CHECK (resource_type IN ('PAGE', 'INITIATIVE', 'CHANGE', 'CATALOG', 'EXTERNAL')),
    resource_id text NOT NULL CHECK (btrim(resource_id) <> ''),
    created_by uuid NOT NULL REFERENCES docplane.principals(principal_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (initiative_id, relation, resource_type, resource_id)
);

CREATE TABLE work.initiative_dependencies (
    initiative_id uuid NOT NULL REFERENCES work.initiatives(initiative_id) ON DELETE CASCADE,
    depends_on_initiative_id uuid NOT NULL REFERENCES work.initiatives(initiative_id) ON DELETE CASCADE,
    dependency_kind text NOT NULL DEFAULT 'REQUIRES' CHECK (dependency_kind IN ('REQUIRES', 'BLOCKED_BY', 'RELATED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (initiative_id, depends_on_initiative_id),
    CHECK (initiative_id <> depends_on_initiative_id)
);
