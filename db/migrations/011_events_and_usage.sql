-- Endpoint-first event stream and first-party usage analytics.
--
-- Events are append-only observations and are deliberately excluded from WP8 documentation-state
-- identity. They create review evidence; they never change authored knowledge or lifecycle state.

CREATE SCHEMA IF NOT EXISTS analytics;

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

CREATE TABLE IF NOT EXISTS analytics.retention_policy (
    id                           boolean     PRIMARY KEY DEFAULT TRUE,
    raw_event_days               integer     NOT NULL DEFAULT 90,
    restricted_payload_days      integer     NOT NULL DEFAULT 30,
    daily_aggregate_days         integer,
    updated_at                   timestamptz NOT NULL DEFAULT now(),
    updated_by_principal_id      uuid        REFERENCES docplane.principals(principal_id),

    CONSTRAINT retention_policy_singleton CHECK (id),
    CONSTRAINT retention_raw_positive CHECK (raw_event_days >= 1),
    CONSTRAINT retention_restricted_positive CHECK (restricted_payload_days >= 1),
    CONSTRAINT retention_aggregate_positive
        CHECK (daily_aggregate_days IS NULL OR daily_aggregate_days >= 1)
);

INSERT INTO analytics.retention_policy (id)
VALUES (TRUE)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS analytics.restricted_payloads (
    payload_id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id             uuid        NOT NULL UNIQUE REFERENCES docplane.events(event_id) ON DELETE CASCADE,
    payload_kind         text        NOT NULL,
    payload              jsonb       NOT NULL,
    expires_at           timestamptz NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT restricted_payload_kind_valid
        CHECK (payload_kind IN ('SEARCH_QUERY', 'AI_QUESTION', 'FEEDBACK_TEXT')),
    CONSTRAINT restricted_payload_expiry_future CHECK (expires_at > created_at),
    CONSTRAINT restricted_payload_bounded CHECK (octet_length(payload::text) <= 32768)
);

CREATE INDEX IF NOT EXISTS restricted_payload_expiry_idx
    ON analytics.restricted_payloads (expires_at);

CREATE TABLE IF NOT EXISTS analytics.daily_page_usage (
    usage_date        date        NOT NULL,
    page_resource_id  uuid        NOT NULL,
    actor_class       text        NOT NULL,
    channel           text        NOT NULL,
    event_type        text        NOT NULL,
    event_count       bigint      NOT NULL,
    unique_principals bigint      NOT NULL DEFAULT 0,
    last_event_at     timestamptz NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (usage_date, page_resource_id, actor_class, channel, event_type),
    CONSTRAINT daily_usage_actor_class_valid
        CHECK (actor_class IN ('HUMAN', 'AGENT', 'AUTOMATION', 'ANONYMOUS')),
    CONSTRAINT daily_usage_channel_valid
        CHECK (channel IN ('WEB', 'API', 'MCP', 'SEARCH', 'AI', 'CLI', 'WEBHOOK', 'SYSTEM')),
    CONSTRAINT daily_usage_count_nonnegative CHECK (event_count >= 0),
    CONSTRAINT daily_usage_unique_nonnegative CHECK (unique_principals >= 0)
);

CREATE OR REPLACE FUNCTION analytics.rollup_page_usage(target_date date)
RETURNS integer AS $$
DECLARE
    changed integer;
BEGIN
    INSERT INTO analytics.daily_page_usage
        (usage_date, page_resource_id, actor_class, channel, event_type,
         event_count, unique_principals, last_event_at, updated_at)
    SELECT occurred_at::date,
           page_resource_id,
           actor_class,
           channel,
           event_type,
           count(*),
           count(DISTINCT principal_id),
           max(occurred_at),
           now()
      FROM docplane.events
     WHERE occurred_at >= target_date::timestamptz
       AND occurred_at < (target_date + 1)::timestamptz
       AND page_resource_id IS NOT NULL
       AND event_type IN ('PAGE_VIEW', 'PAGE_READ_API', 'PAGE_READ_MCP',
                          'SEARCH_RESULT_CLICKED', 'AI_PAGE_CITED')
     GROUP BY occurred_at::date, page_resource_id, actor_class, channel, event_type
    ON CONFLICT (usage_date, page_resource_id, actor_class, channel, event_type)
    DO UPDATE SET event_count = EXCLUDED.event_count,
                  unique_principals = EXCLUDED.unique_principals,
                  last_event_at = EXCLUDED.last_event_at,
                  updated_at = now();
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed;
END;
$$ LANGUAGE plpgsql;

COMMENT ON TABLE docplane.events IS
    'Append-only product and usage observations; excluded from WP8 documentation-state identity.';
COMMENT ON TABLE analytics.restricted_payloads IS
    'Short-retention user-entered search, AI and feedback text; access must be separately authorized.';
