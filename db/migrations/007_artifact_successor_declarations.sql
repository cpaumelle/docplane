-- Successor declarations for generated artifacts (Sprint 5 canary, fourth
-- finding): retiring a declaration releases its pages, but the global UNIQUE
-- on artifact_key made the key permanently unusable — no successor
-- declaration could ever be created, contradicting the ratified R5
-- remediation path ("run the declaring generator, or retire the declaration"
-- implies a regenerated successor can take the key back).
--
-- The correct invariant is AT MOST ONE ACTIVE DECLARATION PER KEY; retired
-- generations keep their rows as audit history. Additive under the
-- compatibility contract: the constraint is weakened, never strengthened —
-- every row set valid before this migration remains valid after it, and an
-- older image running against this schema still refuses duplicate ACTIVE
-- keys through the same unique-violation path it always used.

ALTER TABLE model.generated_artifacts
    DROP CONSTRAINT generated_artifacts_artifact_key_key;

CREATE UNIQUE INDEX generated_artifacts_one_active_key
    ON model.generated_artifacts (artifact_key)
    WHERE status = 'DECLARED';
