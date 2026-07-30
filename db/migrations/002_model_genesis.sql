-- Sprint 2 of the four-domain model (docs/architecture/DOMAIN_MODEL.md):
-- the model domain — a card index of what the fabric structurally is.
-- Storage is generic (one entities table, kind-specific detail in bounded
-- attributes); validity is contract-bound at the API via per-kind checklists
-- harvested from the fabric (docs/architecture/CARD_TYPE_HARVEST_REPORT.md).
-- Additive only: no existing schema, table, column or constraint is touched.

CREATE SCHEMA model;

CREATE TABLE model.entities (
    entity_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_kind text NOT NULL CHECK (entity_kind IN (
        'SYSTEM', 'SERVICE', 'NODE', 'VM', 'SITE', 'NETWORK', 'DATABASE',
        'SCHEMA', 'API', 'ROUTE', 'DEVICE_MODEL', 'INTERFACE', 'ARTIFACT')),
    entity_key text NOT NULL CHECK (entity_key ~ '^[a-z0-9][a-z0-9_.-]{0,126}$'),
    display_name text NOT NULL CHECK (btrim(display_name) <> ''),
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (pg_column_size(attributes) <= 65536),
    owner_principal_id uuid REFERENCES docplane.principals(principal_id),
    created_by uuid NOT NULL REFERENCES docplane.principals(principal_id),
    idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
    status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RETIRED')),
    retired_at timestamptz,
    version bigint NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (entity_kind, entity_key),
    UNIQUE (created_by, idempotency_key),
    CHECK (status <> 'RETIRED' OR retired_at IS NOT NULL)
);
CREATE INDEX entities_kind_status_idx ON model.entities(entity_kind, status, entity_key);

CREATE TABLE model.entity_links (
    from_entity_id uuid NOT NULL REFERENCES model.entities(entity_id) ON DELETE CASCADE,
    relation text NOT NULL CHECK (relation IN (
        'WIRED_TO', 'RUNS_ON', 'MEMBER_OF', 'DEPENDS_ON', 'EXPOSES',
        'STORES_IN', 'GENERATED_FROM', 'WATCHES')),
    to_entity_id uuid NOT NULL REFERENCES model.entities(entity_id) ON DELETE CASCADE,
    note text,
    created_by uuid NOT NULL REFERENCES docplane.principals(principal_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (from_entity_id, relation, to_entity_id),
    CHECK (from_entity_id <> to_entity_id)
);
CREATE INDEX entity_links_to_idx ON model.entity_links(to_entity_id, relation);

CREATE TABLE model.entity_page_links (
    entity_id uuid NOT NULL REFERENCES model.entities(entity_id) ON DELETE CASCADE,
    relation text NOT NULL CHECK (relation IN ('DESCRIBES', 'OPERATES', 'DECIDES', 'CATALOGUES')),
    page_resource_id uuid NOT NULL REFERENCES docs.pages(resource_id),
    created_by uuid NOT NULL REFERENCES docplane.principals(principal_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entity_id, relation, page_resource_id)
);
CREATE INDEX entity_page_links_page_idx ON model.entity_page_links(page_resource_id);

CREATE TABLE model.generated_artifacts (
    artifact_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_key text NOT NULL CHECK (artifact_key ~ '^[a-z0-9][a-z0-9_.-]{0,126}$'),
    generator_name text NOT NULL CHECK (btrim(generator_name) <> ''),
    generator_version text NOT NULL CHECK (btrim(generator_version) <> ''),
    config_hash text CHECK (config_hash IS NULL OR config_hash ~ '^[0-9a-f]{16,64}$'),
    source_entity_id uuid NOT NULL REFERENCES model.entities(entity_id),
    redaction_policy text NOT NULL DEFAULT 'canonical' CHECK (btrim(redaction_policy) <> ''),
    target_page_paths jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (pg_column_size(target_page_paths) <= 16384),
    declared_by uuid NOT NULL REFERENCES docplane.principals(principal_id),
    idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
    status text NOT NULL DEFAULT 'DECLARED' CHECK (status IN ('DECLARED', 'RETIRED')),
    retired_at timestamptz,
    version bigint NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (artifact_key),
    UNIQUE (declared_by, idempotency_key),
    CHECK (status <> 'RETIRED' OR retired_at IS NOT NULL)
);
CREATE INDEX generated_artifacts_source_idx ON model.generated_artifacts(source_entity_id, status);
