-- Review remediation for the four-domain sprints (PR #74 review):
-- (1) a generalised mutation-receipt store so every new verb replays its
--     original response and rejects a key reused with different intent —
--     the trust-API pattern promoted to shared infrastructure;
-- (2) generated-artifact targets bound to stable page identities instead of
--     path strings, with one active declaration per page;
-- (3) the ripple-dedup index widened to cover CLAIMED requests so an entity
--     changing again mid-execution does not mint a second live request.
-- Additive except two safe swaps: an index rebuilt under the same predicate
-- family, and no data is mutated or removed.

CREATE TABLE docplane.mutation_receipts (
    principal_id uuid NOT NULL REFERENCES docplane.principals(principal_id),
    idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
    operation_type text NOT NULL CHECK (btrim(operation_type) <> ''),
    resource_ref text,
    request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    response jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (principal_id, idempotency_key)
);

CREATE TABLE model.artifact_targets (
    artifact_id uuid NOT NULL REFERENCES model.generated_artifacts(artifact_id) ON DELETE CASCADE,
    page_resource_id uuid NOT NULL REFERENCES docs.pages(resource_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (artifact_id, page_resource_id),
    -- One active declaration per generated page: rows exist only while the
    -- declaration stands (retiring the artifact removes its target rows).
    UNIQUE (page_resource_id)
);

DROP INDEX docs.verification_requests_ripple_open_idx;
CREATE UNIQUE INDEX verification_requests_ripple_live_idx
    ON docs.verification_requests(entity_id)
    WHERE status IN ('OPEN', 'CLAIMED') AND reason = 'RIPPLE';
