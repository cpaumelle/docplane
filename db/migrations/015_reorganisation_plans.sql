-- First-class governed documentation reorganisation plans.
--
-- Plans are durable control-plane objects. They describe and analyse proposed
-- structural changes, then convert validated operations into the ordinary
-- DocPlane change-proposal/WP8 path. They never mutate pages directly.

CREATE TABLE IF NOT EXISTS docs.reorganisation_plans (
    plan_id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_key           text        NOT NULL,
    title                   text        NOT NULL,
    purpose                 text        NOT NULL,
    author_principal_id     uuid        NOT NULL REFERENCES docplane.principals(principal_id),
    status                  text        NOT NULL DEFAULT 'DRAFT',
    version                 integer     NOT NULL DEFAULT 1,
    idempotency_key         text        NOT NULL,
    base_state_identity     text,
    analysis_receipt        jsonb,
    validation_receipt      jsonb,
    linked_change_id        uuid        REFERENCES docs.change_proposals(change_id),
    execution_receipt       jsonb,
    certification_receipt   jsonb,
    stabilization_receipt   jsonb,
    compensating_plan_id    uuid        REFERENCES docs.reorganisation_plans(plan_id),
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT reorganisation_plan_workspace_key_valid
        CHECK (workspace_key ~ '^[a-z0-9][a-z0-9_-]{0,62}$'),
    CONSTRAINT reorganisation_plan_title_nonempty CHECK (btrim(title) <> ''),
    CONSTRAINT reorganisation_plan_purpose_nonempty CHECK (btrim(purpose) <> ''),
    CONSTRAINT reorganisation_plan_idempotency_nonempty CHECK (btrim(idempotency_key) <> ''),
    CONSTRAINT reorganisation_plan_version_positive CHECK (version >= 1),
    CONSTRAINT reorganisation_plan_status_valid CHECK (
        status IN (
            'DRAFT', 'ANALYZED', 'VALIDATED', 'SUBMITTED', 'APPROVED',
            'EXECUTING', 'CERTIFYING', 'STABILIZING', 'COMPLETED',
            'FAILED', 'COMPENSATION_REQUIRED', 'CANCELLED'
        )
    ),
    UNIQUE (author_principal_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS reorganisation_plans_workspace_status_idx
    ON docs.reorganisation_plans (workspace_key, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS docs.reorganisation_operations (
    operation_id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id                  uuid        NOT NULL REFERENCES docs.reorganisation_plans(plan_id) ON DELETE CASCADE,
    sequence                 integer     NOT NULL,
    idempotency_key          text        NOT NULL,
    operation_type           text        NOT NULL,
    page_resource_id         uuid,
    expected_revision        text,
    payload                  jsonb       NOT NULL,
    analysis_result          jsonb,
    inverse_operation        jsonb,
    created_at               timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT reorganisation_operation_sequence_nonnegative CHECK (sequence >= 0),
    CONSTRAINT reorganisation_operation_idempotency_nonempty CHECK (btrim(idempotency_key) <> ''),
    CONSTRAINT reorganisation_operation_type_valid CHECK (
        operation_type IN (
            'MOVE_PAGE', 'REPARENT_NAV', 'REORDER_SECTIONS',
            'ADD_REDIRECT', 'REMOVE_REDIRECT', 'ARCHIVE_PAGE', 'RESTORE_PAGE'
        )
    ),
    CONSTRAINT reorganisation_operation_page_binding CHECK (
        operation_type IN ('REORDER_SECTIONS', 'ADD_REDIRECT', 'REMOVE_REDIRECT')
        OR (page_resource_id IS NOT NULL AND expected_revision IS NOT NULL)
    ),
    UNIQUE (plan_id, sequence),
    UNIQUE (plan_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS reorganisation_operations_plan_idx
    ON docs.reorganisation_operations (plan_id, sequence);

CREATE TABLE IF NOT EXISTS docs.reorganisation_reviews (
    review_id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id                  uuid        NOT NULL REFERENCES docs.reorganisation_plans(plan_id) ON DELETE CASCADE,
    reviewer_principal_id    uuid        NOT NULL REFERENCES docplane.principals(principal_id),
    decision                 text        NOT NULL,
    note                     text,
    reviewed_plan_version    integer     NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT reorganisation_review_decision_valid
        CHECK (decision IN ('APPROVE', 'REQUEST_CHANGES', 'COMMENT')),
    CONSTRAINT reorganisation_review_version_positive CHECK (reviewed_plan_version >= 1)
);

CREATE INDEX IF NOT EXISTS reorganisation_reviews_plan_idx
    ON docs.reorganisation_reviews (plan_id, created_at DESC);

COMMENT ON TABLE docs.reorganisation_plans IS
    'Governed structural-change plans. Analysis and approval do not mutate authored state.';
COMMENT ON TABLE docs.reorganisation_operations IS
    'Ordered structural operations with exact revision bindings and generated inverse operations.';
