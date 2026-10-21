-- Optional, server-enforced write scopes for automation principals. Existing
-- principals default to CONTRIBUTOR; SCOPED_AUTOMATION remains fail-closed even
-- if all of its grants are later removed. Scoped writes are bound to the
-- declared artifact key and operation, with generated pages further bounded
-- by path prefix.
ALTER TABLE docplane.principals
    ADD COLUMN authorization_mode text NOT NULL DEFAULT 'CONTRIBUTOR'
    CHECK (
        authorization_mode IN ('CONTRIBUTOR', 'SCOPED_AUTOMATION')
        AND (authorization_mode <> 'SCOPED_AUTOMATION' OR principal_kind = 'AUTOMATION')
    );

CREATE TABLE docplane.principal_artifact_scopes (
    principal_id uuid NOT NULL REFERENCES docplane.principals(principal_id) ON DELETE CASCADE,
    artifact_key text NOT NULL CHECK (artifact_key ~ '^[a-z0-9][a-z0-9_.-]{0,126}$'),
    operation text NOT NULL CHECK (operation IN ('GENERATE', 'OBSERVE')),
    source_entity_key text CHECK (source_entity_key IS NULL OR source_entity_key ~ '^[a-z0-9][a-z0-9_.-]{0,126}$'),
    observation_kind text,
    page_path_prefix text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (principal_id, artifact_key, operation),
    CHECK (
        (operation = 'GENERATE' AND observation_kind IS NULL
         AND source_entity_key IS NOT NULL
         AND page_path_prefix IS NOT NULL
         AND page_path_prefix ~ '^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*/$'
         AND position('..' in page_path_prefix) = 0)
        OR
        (operation = 'OBSERVE' AND observation_kind IS NOT NULL
         AND source_entity_key IS NULL
         AND observation_kind IN ('FRESHNESS_CHECK', 'GENERATION')
         AND page_path_prefix IS NULL)
    )
);

CREATE INDEX principal_artifact_scopes_artifact_idx
    ON docplane.principal_artifact_scopes (artifact_key, operation);

COMMENT ON TABLE docplane.principal_artifact_scopes IS
    'Optional API write authorization scopes for automation principals, enforced by docs-api.';
