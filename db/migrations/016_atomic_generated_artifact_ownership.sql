-- Atomic generated-artifact membership and publication ownership.
-- generator_version records build attribution; projection_contract_version
-- alone identifies projection semantics. Existing declarations are contract v1.
ALTER TABLE model.generated_artifacts
    ADD COLUMN projection_contract_version integer NOT NULL DEFAULT 1
        CHECK (projection_contract_version >= 1);

-- A generated ownership plan is immutable intent attached to a governed
-- publication change and is applied inside the page-publication transaction.
ALTER TABLE docs.changes
    ADD COLUMN generated_ownership_plan jsonb
        CHECK (generated_ownership_plan IS NULL OR pg_column_size(generated_ownership_plan) <= 65536);
