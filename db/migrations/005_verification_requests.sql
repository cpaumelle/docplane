-- Sprint 7 of the four-domain model (docs/architecture/DOMAIN_MODEL.md):
-- verification-on-demand. A verification request is DocPlane's record that
-- someone asked "check this against the fabric" — scoped to one page, a
-- path prefix, or a model entity's described pages — with the briefing
-- captured at mint time. DocPlane records requests and results; execution
-- belongs to whatever agent picks the request up. Expiry stays a prompt and
-- there is no scheduled re-verification anywhere.
-- Additive only: one new table and its indexes.

CREATE TABLE docs.verification_requests (
    request_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Exactly one scope.
    page_resource_id uuid REFERENCES docs.pages(resource_id),
    path_prefix text CHECK (path_prefix IS NULL OR path_prefix ~ '^[a-z0-9/_-]+$'),
    entity_id uuid REFERENCES model.entities(entity_id),
    reason text NOT NULL DEFAULT 'MANUAL' CHECK (reason IN ('MANUAL', 'RIPPLE', 'EXPIRY')),
    status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLAIMED', 'COMPLETED', 'CANCELLED')),
    note text CHECK (note IS NULL OR length(note) <= 4000),
    briefing jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (pg_column_size(briefing) <= 65536),
    resolution jsonb,
    requested_by uuid NOT NULL REFERENCES docplane.principals(principal_id),
    claimed_by uuid REFERENCES docplane.principals(principal_id),
    idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
    requested_at timestamptz NOT NULL DEFAULT now(),
    claimed_at timestamptz,
    completed_at timestamptz,
    UNIQUE (requested_by, idempotency_key),
    CHECK (num_nonnulls(page_resource_id, path_prefix, entity_id) = 1),
    CHECK (status <> 'CLAIMED' OR (claimed_by IS NOT NULL AND claimed_at IS NOT NULL)),
    CHECK (status NOT IN ('COMPLETED', 'CANCELLED') OR completed_at IS NOT NULL)
);
CREATE INDEX verification_requests_status_idx ON docs.verification_requests(status, requested_at DESC);
-- One live ripple request per entity: reality moving twice before anyone
-- checks still needs only one check.
CREATE UNIQUE INDEX verification_requests_ripple_open_idx
    ON docs.verification_requests(entity_id)
    WHERE status = 'OPEN' AND reason = 'RIPPLE';
