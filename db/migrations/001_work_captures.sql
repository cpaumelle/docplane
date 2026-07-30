-- Sprint 1 of the four-domain model (docs/architecture/DOMAIN_MODEL.md):
-- the GTD capture inbox. Captures are zero-decision inbox rows, deliberately
-- lighter than initiatives; triage promotes, attaches or discards them.
-- Additive only: no existing table, column or constraint is touched.

CREATE TABLE work.captures (
    capture_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    body text NOT NULL CHECK (btrim(body) <> '' AND length(body) <= 20000),
    kind text NOT NULL DEFAULT 'IDEA' CHECK (kind IN ('IDEA', 'NEXT_ACTION', 'FINDING', 'QUESTION')),
    origin jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (pg_column_size(origin) <= 8192),
    status text NOT NULL DEFAULT 'INBOX' CHECK (status IN ('INBOX', 'PROMOTED', 'ATTACHED', 'DISCARDED')),
    author_principal_id uuid NOT NULL REFERENCES docplane.principals(principal_id),
    idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
    initiative_id uuid REFERENCES work.initiatives(initiative_id),
    activity_id uuid REFERENCES work.initiative_activities(activity_id),
    triaged_by_principal_id uuid REFERENCES docplane.principals(principal_id),
    triaged_at timestamptz,
    triage_note text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (author_principal_id, idempotency_key),
    CONSTRAINT captures_triage_state_consistent CHECK (
        (status = 'INBOX' AND initiative_id IS NULL AND activity_id IS NULL
            AND triaged_by_principal_id IS NULL AND triaged_at IS NULL)
        OR (status = 'PROMOTED' AND initiative_id IS NOT NULL AND activity_id IS NULL
            AND triaged_by_principal_id IS NOT NULL AND triaged_at IS NOT NULL)
        OR (status = 'ATTACHED' AND initiative_id IS NOT NULL AND activity_id IS NOT NULL
            AND triaged_by_principal_id IS NOT NULL AND triaged_at IS NOT NULL)
        OR (status = 'DISCARDED' AND initiative_id IS NULL AND activity_id IS NULL
            AND triaged_by_principal_id IS NOT NULL AND triaged_at IS NOT NULL)
    )
);

CREATE INDEX captures_status_created_idx ON work.captures(status, created_at DESC);
