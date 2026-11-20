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
-- authenticate over the container-local Unix socket and container-loopback TCP
-- under the target HBA observed 2026-11-20 (both trust). Non-loopback TCP uses
-- SCRAM, and no host port was published at that check. Access to the container
-- or its network namespace therefore crosses this boundary; it is not a socket-only
-- identity. Re-verify effective HBA, network exposure and privileges at acceptance.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docplane_credential_expiry_observer') THEN
        RAISE EXCEPTION 'credential expiry observer role already exists; reconcile privileges before migration';
    END IF;
    CREATE ROLE docplane_credential_expiry_observer LOGIN NOINHERIT CONNECTION LIMIT 2;
END $$;
ALTER ROLE docplane_credential_expiry_observer
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD NULL;
ALTER ROLE docplane_credential_expiry_observer SET default_transaction_read_only = on;
ALTER ROLE docplane_credential_expiry_observer SET statement_timeout = '10s';

GRANT USAGE ON SCHEMA work TO docplane_credential_expiry_observer;
GRANT SELECT ON work.collaborator_token_expiry, work.collaborator_grant_expiry
    TO docplane_credential_expiry_observer;

-- Fail the migration, rather than altering shared PUBLIC grants, if the fresh
-- role can access application objects through PUBLIC or an unexpected default.
-- pg_catalog is PostgreSQL's built-in metadata surface; application schemas are
-- audited separately. The operator must also verify the effective pg_hba rules.
DO $$
DECLARE observer oid := 'docplane_credential_expiry_observer'::regrole;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member = observer OR roleid = observer) THEN
        RAISE EXCEPTION 'credential expiry observer has unexpected role membership';
    END IF;
    IF has_database_privilege(observer, current_database(), 'CREATE') THEN
        RAISE EXCEPTION 'credential expiry observer can create database objects';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_namespace n
               WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
                 AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp_%'
                 AND has_schema_privilege(observer, n.oid, 'CREATE')) THEN
        RAISE EXCEPTION 'credential expiry observer can create in an application schema';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE c.relkind IN ('r','p','v','m','f')
                 AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                 AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp_%'
                 AND (n.nspname, c.relname) NOT IN (('work','collaborator_token_expiry'),('work','collaborator_grant_expiry'))
                 AND (has_table_privilege(observer,c.oid,'SELECT') OR has_table_privilege(observer,c.oid,'INSERT')
                      OR has_table_privilege(observer,c.oid,'UPDATE') OR has_table_privilege(observer,c.oid,'DELETE')
                      OR has_table_privilege(observer,c.oid,'TRUNCATE') OR has_table_privilege(observer,c.oid,'TRIGGER')
                      OR has_table_privilege(observer,c.oid,'REFERENCES'))) THEN
        RAISE EXCEPTION 'credential expiry observer has unexpected application table privilege';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE (n.nspname, c.relname) IN (( 'work','collaborator_token_expiry'),('work','collaborator_grant_expiry'))
                 AND (has_table_privilege(observer,c.oid,'INSERT') OR has_table_privilege(observer,c.oid,'UPDATE')
                      OR has_table_privilege(observer,c.oid,'DELETE') OR has_table_privilege(observer,c.oid,'TRUNCATE')
                      OR has_table_privilege(observer,c.oid,'TRIGGER') OR has_table_privilege(observer,c.oid,'REFERENCES'))) THEN
        RAISE EXCEPTION 'credential expiry observer has write privilege on an expiry view';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE c.relkind = 'S' AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                 AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp_%'
                 AND (has_sequence_privilege(observer,c.oid,'SELECT') OR has_sequence_privilege(observer,c.oid,'USAGE')
                      OR has_sequence_privilege(observer,c.oid,'UPDATE'))) THEN
        RAISE EXCEPTION 'credential expiry observer has unexpected sequence privilege';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
               WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                 AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp_%'
                 AND has_function_privilege(observer,p.oid,'EXECUTE')) THEN
        RAISE EXCEPTION 'credential expiry observer can execute an application security-definer function';
    END IF;
END $$;
