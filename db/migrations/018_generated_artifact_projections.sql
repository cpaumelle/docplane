-- Generator-owned structured projections (initiative e004787b, decision D1).
--
-- A generated artifact may carry one machine-readable projection alongside the
-- KNOW pages it renders: for the schema catalogue, the detailed schema graph
-- (tables, columns, keys, indexes, relations) that agents query at table and
-- column granularity. This is deliberately NOT a set of TABLE/COLUMN MODEL
-- entities: DATABASE and SCHEMA remain the MODEL identities ("depth stops at
-- the schema"), and the projection is replaced wholesale by its generator.
--
-- Custody follows the artifact: only the artifact's declaring AUTOMATION
-- principal may write it, and only while the artifact is DECLARED. The row is
-- the CURRENT projection; its history is the artifact's GENERATION evidence.
-- source_fingerprint is re-derived by the API from document->'schemas' on every
-- write, so a stored projection provably corresponds to the fingerprint the
-- following GENERATION observation names.
CREATE TABLE model.artifact_projections (
    artifact_id uuid PRIMARY KEY
        REFERENCES model.generated_artifacts(artifact_id) ON DELETE CASCADE,
    projection_kind text NOT NULL CHECK (projection_kind IN ('SCHEMA_STRUCTURE')),
    projection_contract_version integer NOT NULL
        CHECK (projection_contract_version >= 1),
    source_fingerprint text NOT NULL CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    config_hash text NOT NULL CHECK (config_hash ~ '^[0-9a-f]{64}$'),
    document_sha256 text NOT NULL CHECK (document_sha256 ~ '^[0-9a-f]{64}$'),
    document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    provenance jsonb NOT NULL CHECK (jsonb_typeof(provenance) = 'object'),
    published_by uuid NOT NULL REFERENCES docplane.principals(principal_id),
    version bigint NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX artifact_projections_kind_idx
    ON model.artifact_projections (projection_kind);

CREATE TRIGGER artifact_projections_touch_updated_at
BEFORE UPDATE ON model.artifact_projections
FOR EACH ROW EXECUTE FUNCTION docplane.touch_updated_at();

COMMENT ON TABLE model.artifact_projections IS
    'Current generator-owned structured projection of a generated artifact (e004787b D1).';
