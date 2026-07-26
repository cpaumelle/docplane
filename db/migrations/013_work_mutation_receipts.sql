-- Idempotent receipts for active-work mutations.

CREATE TABLE IF NOT EXISTS work.mutation_receipts (
    principal_id    uuid        NOT NULL REFERENCES docplane.principals(principal_id),
    idempotency_key text        NOT NULL,
    operation_type  text        NOT NULL,
    resource_id     text,
    request_hash    text        NOT NULL,
    response        jsonb       NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (principal_id, idempotency_key),
    CONSTRAINT work_receipts_key_nonempty CHECK (btrim(idempotency_key) <> ''),
    CONSTRAINT work_receipts_operation_nonempty CHECK (btrim(operation_type) <> ''),
    CONSTRAINT work_receipts_hash_sha256 CHECK (request_hash ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS work_mutation_receipts_resource_idx
    ON work.mutation_receipts (resource_id, created_at DESC)
    WHERE resource_id IS NOT NULL;
