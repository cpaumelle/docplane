-- Value-blind, least-privilege read surface for WORK-collaborator credential expiry.
--
-- The WORK inbox's participant credentials (docplane.api_tokens rows of
-- WORK_COLLABORATOR principals, and work.collaboration_grants) expire, and an
-- expired one silently turns every inbox call into a 401/403. These two views
-- expose ONLY what an expiry monitor needs: the participant's display name, the
-- initiative key, counts and timestamps. No token id, hash, prefix or
-- description is reachable through them.
--
-- The views run with their owner's privileges (security_invoker is off), so the
-- observer role below needs no privilege on any base table.

CREATE VIEW work.collaborator_token_expiry AS
SELECT
    p.display_name AS participant,
    count(t.token_id) FILTER (
        WHERE t.revoked_at IS NULL AND (t.expires_at IS NULL OR t.expires_at > now())
    )::integer AS active_tokens,
    count(t.token_id) FILTER (
        WHERE t.revoked_at IS NULL AND t.expires_at IS NOT NULL AND t.expires_at <= now()
    )::integer AS lapsed_tokens,
    min(t.expires_at) FILTER (
        WHERE t.revoked_at IS NULL AND t.expires_at > now()
    ) AS earliest_active_expiry
FROM docplane.principals p
LEFT JOIN docplane.api_tokens t ON t.principal_id = p.principal_id
WHERE p.authorization_mode = 'WORK_COLLABORATOR'
  AND p.status = 'ACTIVE'
-- Grouped by identity, not by name: two principals sharing a display name stay
-- two rows, so the consumer can refuse the ambiguous label instead of merging them.
GROUP BY p.principal_id, p.display_name;

COMMENT ON VIEW work.collaborator_token_expiry IS
    'Per ACTIVE WORK_COLLABORATOR principal: unrevoked unexpired token count, unrevoked expired (lapsed) token count, and the earliest expiry among active tokens. Value-blind: exposes no token identifier, hash, prefix or description.';

CREATE VIEW work.collaborator_grant_expiry AS
SELECT
    p.display_name AS participant,
    i.initiative_key,
    g.participant_role,
    g.expires_at
FROM work.collaboration_grants g
JOIN docplane.principals p ON p.principal_id = g.principal_id
JOIN work.initiatives i ON i.initiative_id = g.initiative_id
WHERE g.revoked_at IS NULL
  AND p.authorization_mode = 'WORK_COLLABORATOR'
  AND p.status = 'ACTIVE';

COMMENT ON VIEW work.collaborator_grant_expiry IS
    'Every unrevoked collaboration grant of an ACTIVE WORK_COLLABORATOR principal, with its expiry (NULL = no expiry). Lapsed grants stay listed until renewed or revoked, so an expiry alert cannot resolve merely because the grant ran out.';

-- The observer role. LOGIN without a password: under the deployed pg_hba it can
-- connect only over the container-local socket (trust), i.e. only by a caller
-- that can already `docker exec` into the database container. Every TCP rule is
-- scram-sha-256, which a password-less role cannot satisfy. So the role adds no
-- network-reachable credential and needs no secret, and it narrows what the
-- host-side producer can read to these two views.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docplane_credential_expiry_observer') THEN
        CREATE ROLE docplane_credential_expiry_observer LOGIN NOINHERIT CONNECTION LIMIT 2;
    END IF;
END $$;
ALTER ROLE docplane_credential_expiry_observer
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD NULL;
ALTER ROLE docplane_credential_expiry_observer SET default_transaction_read_only = on;
ALTER ROLE docplane_credential_expiry_observer SET statement_timeout = '10s';

GRANT USAGE ON SCHEMA work TO docplane_credential_expiry_observer;
GRANT SELECT ON work.collaborator_token_expiry, work.collaborator_grant_expiry
    TO docplane_credential_expiry_observer;
