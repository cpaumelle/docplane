-- Endpoint-first append-only domain event stream.
--
-- Events support delta synchronization, audit and integration. Usage instrumentation,
-- aggregation, retention and traffic analysis belong to the dashboard slice.

CREATE TABLE IF NOT EXISTS docplane.events (
    event_seq        bigserial   PRIMARY KEY,
    event_id         uuid        NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    occurred_at      timestamptz NOT NULL DEFAULT now(),
    event_type       text        NOT NULL,
    principal_id     uuid        REFERENCES docplane.principals(principal_id),
    actor_class      text        NOT NULL,
    channel          text        NOT NULL,
    producer_id      text        NOT NULL,
    client_identity  text,
    workspace_key    text,
    resource_type    text,
    resource_id      text,
    page_resource_id uuid,
    page_path        text,
    page_revision    text,
    release_id       text,
    idempotency_key  text        NOT NULL,
    metadata         jsonb       NOT NULL DEFAULT '{}',
    ingested_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT events_type_valid
        CHECK (event_type ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    CONSTRAINT events_actor_class_valid
        CHECK (actor_class IN ('HUMAN', 'AGENT', 'AUTOMATION', 'ANONYMOUS')),
    CONSTRAINT events_channel_valid
        CHECK (channel IN ('WEB', 'API', 'MCP', 'SEARCH', 'AI', 'CLI', 'WEBHOOK', 'SYSTEM')),
    CONSTRAINT events_producer_nonempty CHECK (btrim(producer_id) <> ''),
    CONSTRAINT events_idempotency_nonempty CHECK (btrim(idempotency_key) <> ''),
    CONSTRAINT events_workspace_key_valid
        CHECK (workspace_key IS NULL OR workspace_key ~ '^[a-z0-9][a-z0-9_-]{0,62}$'),
    CONSTRAINT events_metadata_bounded
        CHECK (octet_length(metadata::text) <= 16384),
    UNIQUE (producer_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS events_occurred_idx
    ON docplane.events (occurred_at DESC, event_seq DESC);

CREATE INDEX IF NOT EXISTS events_page_idx
    ON docplane.events (page_resource_id, occurred_at DESC)
    WHERE page_resource_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS events_type_idx
    ON docplane.events (event_type, occurred_at DESC);

CREATE INDEX IF NOT EXISTS events_principal_idx
    ON docplane.events (principal_id, occurred_at DESC)
    WHERE principal_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS events_workspace_idx
    ON docplane.events (workspace_key, occurred_at DESC)
    WHERE workspace_key IS NOT NULL;

COMMENT ON TABLE docplane.events IS
    'Append-only domain and integration events; excluded from WP8 documentation-state identity.';
