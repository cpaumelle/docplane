-- Generated-artifact execution declarations are MODEL intent: who owns
-- observation/generation and the bounded promise those executors make. This
-- table stores no deployed unit state and deliberately backfills no artifact.
CREATE TABLE model.generated_artifact_execution_contracts (
    artifact_id uuid PRIMARY KEY
        REFERENCES model.generated_artifacts(artifact_id) ON DELETE CASCADE,
    contract_schema_version smallint NOT NULL DEFAULT 1
        CHECK (contract_schema_version = 1),
    observation_owner_principal_id uuid NOT NULL
        REFERENCES docplane.principals(principal_id),
    observation_trigger text NOT NULL CHECK (observation_trigger IN (
        'MANUAL', 'SCHEDULED', 'EVENT_DRIVEN', 'HYBRID')),
    observation_max_age_seconds integer NOT NULL CHECK (
        observation_max_age_seconds > 0
        AND observation_max_age_seconds <= 31536000),
    generation_owner_principal_id uuid NOT NULL
        REFERENCES docplane.principals(principal_id),
    generation_trigger text NOT NULL CHECK (generation_trigger IN (
        'MANUAL', 'SCHEDULED', 'EVENT_DRIVEN', 'HYBRID')),
    -- Logical exclusion identity, never an OS lock path.
    exclusion_domain text NOT NULL CHECK (
        exclusion_domain ~ '^[a-z0-9][a-z0-9_.-]{0,126}$'),
    created_by uuid NOT NULL REFERENCES docplane.principals(principal_id),
    updated_by uuid NOT NULL REFERENCES docplane.principals(principal_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER generated_artifact_execution_contracts_touch_updated_at
BEFORE UPDATE ON model.generated_artifact_execution_contracts
FOR EACH ROW EXECUTE FUNCTION docplane.touch_updated_at();
