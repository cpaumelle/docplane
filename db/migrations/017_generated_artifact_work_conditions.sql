-- Durable WORK projection for automatically reconcilable generated-artifact
-- conditions.  This is deliberately separate from human initiatives/captures
-- and from monitoring-specific coverage gaps.
--
-- One (artifact, condition_kind) pair keeps a stable identity across
-- OPEN -> RESOLVED -> OPEN recurrence.  Reconciliation is exact-set at the API;
-- unchanged conditions are zero-write so periodic health checks do not create
-- WORK churn merely by being observed again.

CREATE TABLE work.generated_artifact_conditions (
    condition_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id uuid NOT NULL REFERENCES model.generated_artifacts(artifact_id),
    condition_kind text NOT NULL CHECK (condition_kind ~ '^[A-Z][A-Z0-9_]{2,63}$'),
    briefing jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (pg_column_size(briefing) <= 8192),
    status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'RESOLVED')),
    created_by uuid NOT NULL REFERENCES docplane.principals(principal_id),
    updated_by uuid NOT NULL REFERENCES docplane.principals(principal_id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_transition_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    version bigint NOT NULL DEFAULT 1 CHECK (version >= 1),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (artifact_id, condition_kind),
    CHECK (
        (status = 'OPEN' AND resolved_at IS NULL)
        OR (status = 'RESOLVED' AND resolved_at IS NOT NULL)
    )
);

CREATE INDEX generated_artifact_conditions_status_idx
    ON work.generated_artifact_conditions(status, condition_kind, artifact_id);
