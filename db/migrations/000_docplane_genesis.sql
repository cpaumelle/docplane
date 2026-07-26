--
-- PostgreSQL database dump
--



SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: docplane; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS docplane;


--
-- Name: docs; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA docs;


--
-- Name: work; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA work;


--
-- Name: default_page_workspace(); Type: FUNCTION; Schema: docplane; Owner: -
--

CREATE FUNCTION docplane.default_page_workspace() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.workspace_id IS NULL THEN
        SELECT workspace_id INTO NEW.workspace_id
          FROM docplane.workspaces
         WHERE workspace_key = 'reference';
    END IF;
    IF NEW.publication_state IS NULL THEN
        NEW.publication_state := CASE NEW.status
            WHEN 'active' THEN 'PUBLISHED'
            WHEN 'archived' THEN 'ARCHIVED'
            ELSE 'DRAFT'
        END;
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: touch_updated_at(); Type: FUNCTION; Schema: docplane; Owner: -
--

CREATE FUNCTION docplane.touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;


--
-- Name: corpus_certification_monotonic(); Type: FUNCTION; Schema: docs; Owner: -
--

CREATE FUNCTION docs.corpus_certification_monotonic() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.state_version <= OLD.state_version THEN
        RAISE EXCEPTION
            'corpus_certification.state_version must increase (old=%, new=%)',
            OLD.state_version, NEW.state_version
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;


--
-- Name: expire_due_verifications(timestamp with time zone); Type: FUNCTION; Schema: docs; Owner: -
--

CREATE FUNCTION docs.expire_due_verifications(cutoff timestamp with time zone DEFAULT now()) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    changed integer;
BEGIN
    WITH expired AS (
        UPDATE docs.page_verifications
           SET verification_state = 'EXPIRED'
         WHERE verification_state = 'VERIFIED'
           AND expires_at IS NOT NULL
           AND expires_at <= cutoff
        RETURNING page_resource_id
    )
    UPDATE docs.pages p
       SET verification_state = 'EXPIRED',
           review_due_at = coalesce(p.review_due_at, cutoff)
     WHERE p.resource_id IN (SELECT page_resource_id FROM expired)
       AND p.verification_state = 'VERIFIED';
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed;
END;
$$;


--
-- Name: FUNCTION expire_due_verifications(cutoff timestamp with time zone); Type: COMMENT; Schema: docs; Owner: -
--

COMMENT ON FUNCTION docs.expire_due_verifications(cutoff timestamp with time zone) IS 'Idempotently expires revision-bound verifications; invoke through the maintenance API or scheduler.';


--
-- Name: invalidate_verification_on_revision_change(); Type: FUNCTION; Schema: docs; Owner: -
--

CREATE FUNCTION docs.invalidate_verification_on_revision_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.revision IS DISTINCT FROM OLD.revision THEN
        UPDATE docs.page_verifications
           SET verification_state = 'OUTDATED'
         WHERE page_resource_id = NEW.resource_id
           AND verification_state = 'VERIFIED';

        IF OLD.verification_state = 'VERIFIED' THEN
            NEW.verification_state := 'OUTDATED';
            NEW.review_due_at := now();
        END IF;
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: redirects_monotonic(); Type: FUNCTION; Schema: docs; Owner: -
--

CREATE FUNCTION docs.redirects_monotonic() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.version <= OLD.version THEN
        RAISE EXCEPTION
            'docs.redirects.version must increase (from_path=%, old=%, new=%)',
            OLD.from_path, OLD.version, NEW.version
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;


--
-- Name: require_work_workspace(); Type: FUNCTION; Schema: work; Owner: -
--

CREATE FUNCTION work.require_work_workspace() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    kind text;
BEGIN
    SELECT workspace_kind INTO kind
      FROM docplane.workspaces
     WHERE workspace_id = NEW.workspace_id;
    IF kind IS DISTINCT FROM 'WORK' THEN
        RAISE EXCEPTION 'initiative workspace must have kind WORK (got %)', kind
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: api_tokens; Type: TABLE; Schema: docplane; Owner: -
--

CREATE TABLE docplane.api_tokens (
    token_id uuid DEFAULT gen_random_uuid() NOT NULL,
    principal_id uuid NOT NULL,
    token_hash text NOT NULL,
    token_prefix text NOT NULL,
    scopes text[] NOT NULL,
    description text,
    issued_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    last_used_at timestamp with time zone,
    revoked_at timestamp with time zone,
    CONSTRAINT api_tokens_expiry_after_issue CHECK (((expires_at IS NULL) OR (expires_at > issued_at))),
    CONSTRAINT api_tokens_hash_sha256 CHECK ((token_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT api_tokens_prefix_bounded CHECK (((length(token_prefix) >= 4) AND (length(token_prefix) <= 24))),
    CONSTRAINT api_tokens_scopes_nonempty CHECK ((COALESCE(array_length(scopes, 1), 0) >= 1))
);


--
-- Name: events; Type: TABLE; Schema: docplane; Owner: -
--

CREATE TABLE docplane.events (
    event_seq bigint NOT NULL,
    event_id uuid DEFAULT gen_random_uuid() NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    event_type text NOT NULL,
    principal_id uuid,
    actor_class text NOT NULL,
    channel text NOT NULL,
    producer_id text NOT NULL,
    client_identity text,
    workspace_key text,
    resource_type text,
    resource_id text,
    page_resource_id uuid,
    page_path text,
    page_revision text,
    release_id text,
    idempotency_key text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    ingested_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT events_actor_class_valid CHECK ((actor_class = ANY (ARRAY['HUMAN'::text, 'AGENT'::text, 'AUTOMATION'::text, 'ANONYMOUS'::text]))),
    CONSTRAINT events_channel_valid CHECK ((channel = ANY (ARRAY['WEB'::text, 'API'::text, 'MCP'::text, 'SEARCH'::text, 'AI'::text, 'CLI'::text, 'WEBHOOK'::text, 'SYSTEM'::text]))),
    CONSTRAINT events_idempotency_nonempty CHECK ((btrim(idempotency_key) <> ''::text)),
    CONSTRAINT events_metadata_bounded CHECK ((octet_length((metadata)::text) <= 16384)),
    CONSTRAINT events_producer_nonempty CHECK ((btrim(producer_id) <> ''::text)),
    CONSTRAINT events_type_valid CHECK ((event_type ~ '^[A-Z][A-Z0-9_]{1,63}$'::text)),
    CONSTRAINT events_workspace_key_valid CHECK (((workspace_key IS NULL) OR (workspace_key ~ '^[a-z0-9][a-z0-9_-]{0,62}$'::text)))
);


--
-- Name: TABLE events; Type: COMMENT; Schema: docplane; Owner: -
--

COMMENT ON TABLE docplane.events IS 'Append-only domain and integration events; excluded from WP8 documentation-state identity.';


--
-- Name: events_event_seq_seq; Type: SEQUENCE; Schema: docplane; Owner: -
--

CREATE SEQUENCE docplane.events_event_seq_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: events_event_seq_seq; Type: SEQUENCE OWNED BY; Schema: docplane; Owner: -
--

ALTER SEQUENCE docplane.events_event_seq_seq OWNED BY docplane.events.event_seq;


--
-- Name: principals; Type: TABLE; Schema: docplane; Owner: -
--

CREATE TABLE docplane.principals (
    principal_id uuid DEFAULT gen_random_uuid() NOT NULL,
    principal_kind text NOT NULL,
    display_name text NOT NULL,
    owner_principal_id uuid,
    status text DEFAULT 'ACTIVE'::text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT principals_display_name_nonempty CHECK ((btrim(display_name) <> ''::text)),
    CONSTRAINT principals_kind_valid CHECK ((principal_kind = ANY (ARRAY['HUMAN'::text, 'AGENT'::text, 'AUTOMATION'::text]))),
    CONSTRAINT principals_status_valid CHECK ((status = ANY (ARRAY['ACTIVE'::text, 'SUSPENDED'::text, 'REVOKED'::text])))
);


--
-- Name: workspace_memberships; Type: TABLE; Schema: docplane; Owner: -
--

CREATE TABLE docplane.workspace_memberships (
    workspace_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    role text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid,
    CONSTRAINT workspace_memberships_role_valid CHECK ((role = ANY (ARRAY['READER'::text, 'PROPOSER'::text, 'EDITOR'::text, 'REVIEWER'::text, 'MERGER'::text, 'OWNER'::text])))
);


--
-- Name: workspaces; Type: TABLE; Schema: docplane; Owner: -
--

CREATE TABLE docplane.workspaces (
    workspace_id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_key text NOT NULL,
    name text NOT NULL,
    workspace_kind text NOT NULL,
    visibility text DEFAULT 'INTERNAL'::text NOT NULL,
    mutation_policy text DEFAULT 'PROPOSAL_REQUIRED'::text NOT NULL,
    default_verification_days integer,
    retention_policy jsonb DEFAULT '{}'::jsonb NOT NULL,
    system_workspace boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT workspaces_key_valid CHECK ((workspace_key ~ '^[a-z0-9][a-z0-9_-]{0,62}$'::text)),
    CONSTRAINT workspaces_kind_valid CHECK ((workspace_kind = ANY (ARRAY['REFERENCE'::text, 'OPERATIONS'::text, 'WORK'::text, 'MIGRATION'::text]))),
    CONSTRAINT workspaces_mutation_policy_valid CHECK ((mutation_policy = ANY (ARRAY['PROPOSAL_REQUIRED'::text, 'DIRECT_LOW_RISK'::text, 'DIRECT_ALLOWED'::text]))),
    CONSTRAINT workspaces_name_nonempty CHECK ((btrim(name) <> ''::text)),
    CONSTRAINT workspaces_verification_days_positive CHECK (((default_verification_days IS NULL) OR (default_verification_days >= 1))),
    CONSTRAINT workspaces_visibility_valid CHECK ((visibility = ANY (ARRAY['PRIVATE'::text, 'INTERNAL'::text, 'PUBLIC'::text])))
);


--
-- Name: change_operations; Type: TABLE; Schema: docs; Owner: -
--

CREATE TABLE docs.change_operations (
    operation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    change_id uuid NOT NULL,
    sequence integer NOT NULL,
    idempotency_key text NOT NULL,
    operation_type text NOT NULL,
    page_resource_id uuid,
    expected_revision text,
    expected_section_hash text,
    payload jsonb NOT NULL,
    validation_result jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT change_operations_existing_page_needs_revision CHECK (((operation_type = 'CREATE_FROM_TEMPLATE'::text) OR ((page_resource_id IS NOT NULL) AND (expected_revision IS NOT NULL)))),
    CONSTRAINT change_operations_idempotency_nonempty CHECK ((btrim(idempotency_key) <> ''::text)),
    CONSTRAINT change_operations_sequence_nonnegative CHECK ((sequence >= 0)),
    CONSTRAINT change_operations_type_valid CHECK ((operation_type = ANY (ARRAY['PATCH_METADATA'::text, 'REPLACE_SECTION'::text, 'INSERT_BEFORE_HEADING'::text, 'INSERT_AFTER_HEADING'::text, 'REPLACE_CONTENT_RANGE'::text, 'ADD_LINK'::text, 'REMOVE_LINK'::text, 'CREATE_FROM_TEMPLATE'::text, 'MOVE_PAGE'::text, 'REPARENT_NAV'::text, 'ARCHIVE_PAGE'::text, 'RESTORE_PAGE'::text, 'ADD_REDIRECT'::text, 'REPLACE_DOCUMENT'::text])))
);


--
-- Name: change_proposals; Type: TABLE; Schema: docs; Owner: -
--

CREATE TABLE docs.change_proposals (
    change_id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_key text DEFAULT 'default'::text NOT NULL,
    title text NOT NULL,
    purpose text NOT NULL,
    author_principal_id uuid NOT NULL,
    idempotency_key text NOT NULL,
    status text DEFAULT 'DRAFT'::text NOT NULL,
    base_state_identity text,
    validation_summary jsonb,
    preview_receipt jsonb,
    submitted_at timestamp with time zone,
    resolved_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT change_proposals_idempotency_nonempty CHECK ((btrim(idempotency_key) <> ''::text)),
    CONSTRAINT change_proposals_merged_is_resolved CHECK (((status <> 'MERGED'::text) OR (resolved_at IS NOT NULL))),
    CONSTRAINT change_proposals_purpose_nonempty CHECK ((btrim(purpose) <> ''::text)),
    CONSTRAINT change_proposals_status_valid CHECK ((status = ANY (ARRAY['DRAFT'::text, 'VALIDATED'::text, 'SUBMITTED'::text, 'REJECTED'::text, 'ABANDONED'::text, 'MERGED'::text]))),
    CONSTRAINT change_proposals_title_nonempty CHECK ((btrim(title) <> ''::text)),
    CONSTRAINT change_proposals_workspace_key_valid CHECK ((workspace_key ~ '^[a-z0-9][a-z0-9_-]{0,62}$'::text))
);


--
-- Name: corpus_certification; Type: TABLE; Schema: docs; Owner: -
--

CREATE TABLE docs.corpus_certification (
    id boolean DEFAULT true NOT NULL,
    state text NOT NULL,
    working_corpus_snapshot_id text,
    deployed_build_snapshot_id text,
    deployment_target_snapshot_id text,
    renderer_provenance jsonb,
    publication_config_provenance jsonb,
    validator_contract_version text,
    uncertifiable_target_pages text[] DEFAULT '{}'::text[] NOT NULL,
    last_failure_rules text[] DEFAULT '{}'::text[] NOT NULL,
    state_version bigint DEFAULT 0 NOT NULL,
    last_certified_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    working_state_snapshot_id text,
    deployed_state_snapshot_id text,
    deployment_target_state_snapshot_id text,
    working_state_components jsonb,
    deployed_state_components jsonb,
    deployment_target_state_components jsonb,
    state_identity_contract_version text,
    deployment_cycle_id text,
    validation_policy_identity text,
    validation_policy_components jsonb,
    CONSTRAINT corpus_certification_ahead_page_ids_present CHECK (((state <> 'WORKING_AHEAD'::text) OR ((working_corpus_snapshot_id IS NOT NULL) AND (deployed_build_snapshot_id IS NOT NULL)))),
    CONSTRAINT corpus_certification_ahead_state_ids_differ CHECK (((state <> 'WORKING_AHEAD'::text) OR ((working_state_snapshot_id IS NOT NULL) AND (deployed_state_snapshot_id IS NOT NULL) AND (working_state_snapshot_id <> deployed_state_snapshot_id)))),
    CONSTRAINT corpus_certification_certified_implies_version CHECK (((last_certified_at IS NULL) OR (state_version >= 1))),
    CONSTRAINT corpus_certification_components_complete CHECK (((state = ANY (ARRAY['DEPLOYMENT_FAILED'::text, 'UNAVAILABLE'::text])) OR ((working_state_components ?& ARRAY['page_corpus_identity'::text, 'redirect_register_identity'::text, 'navigation_state_identity'::text]) AND (deployed_state_components ?& ARRAY['page_corpus_identity'::text, 'redirect_register_identity'::text, 'navigation_state_identity'::text])))),
    CONSTRAINT corpus_certification_current_no_active_deployment CHECK (((state <> 'CURRENT'::text) OR (deployment_target_snapshot_id IS NULL))),
    CONSTRAINT corpus_certification_current_page_ids_present CHECK (((state <> 'CURRENT'::text) OR ((working_corpus_snapshot_id IS NOT NULL) AND (deployed_build_snapshot_id IS NOT NULL)))),
    CONSTRAINT corpus_certification_current_state_ids_match CHECK (((state <> 'CURRENT'::text) OR ((working_state_snapshot_id IS NOT NULL) AND (deployed_state_snapshot_id IS NOT NULL) AND (working_state_snapshot_id = deployed_state_snapshot_id) AND (deployment_target_state_snapshot_id IS NULL)))),
    CONSTRAINT corpus_certification_failed_keeps_diagnosis CHECK (((state <> 'DEPLOYMENT_FAILED'::text) OR ((working_corpus_snapshot_id IS NOT NULL) AND (deployed_build_snapshot_id IS NOT NULL) AND (deployment_target_snapshot_id IS NOT NULL) AND (COALESCE(array_length(last_failure_rules, 1), 0) >= 1)))),
    CONSTRAINT corpus_certification_no_empty_identities CHECK (((COALESCE(working_corpus_snapshot_id, 'x'::text) <> ''::text) AND (COALESCE(deployed_build_snapshot_id, 'x'::text) <> ''::text) AND (COALESCE(deployment_target_snapshot_id, 'x'::text) <> ''::text))),
    CONSTRAINT corpus_certification_pin_names_all_components CHECK (((deployment_target_state_snapshot_id IS NULL) OR ((deployment_target_state_components IS NOT NULL) AND (deployment_target_state_components ?& ARRAY['page_corpus_identity'::text, 'redirect_register_identity'::text, 'navigation_state_identity'::text])))),
    CONSTRAINT corpus_certification_singleton CHECK (id),
    CONSTRAINT corpus_certification_state_valid CHECK ((state = ANY (ARRAY['CURRENT'::text, 'WORKING_AHEAD'::text, 'DEPLOYMENT_FAILED'::text, 'UNAVAILABLE'::text]))),
    CONSTRAINT corpus_certification_state_version_nonneg CHECK ((state_version >= 0)),
    CONSTRAINT corpus_certification_target_needs_working CHECK (((deployment_target_snapshot_id IS NULL) OR (working_corpus_snapshot_id IS NOT NULL))),
    CONSTRAINT corpus_certification_unavailable_not_certified CHECK (((state <> 'UNAVAILABLE'::text) OR (last_certified_at IS NULL) OR (state_version > 0))),
    CONSTRAINT corpus_certification_usable_contract_nonempty CHECK (((state = ANY (ARRAY['DEPLOYMENT_FAILED'::text, 'UNAVAILABLE'::text])) OR (COALESCE(validator_contract_version, ''::text) <> ''::text))),
    CONSTRAINT corpus_certification_usable_needs_provenance CHECK (((state = ANY (ARRAY['DEPLOYMENT_FAILED'::text, 'UNAVAILABLE'::text])) OR ((renderer_provenance IS NOT NULL) AND (publication_config_provenance IS NOT NULL) AND (validator_contract_version IS NOT NULL)))),
    CONSTRAINT corpus_certification_usable_needs_state_identity CHECK (((state = ANY (ARRAY['DEPLOYMENT_FAILED'::text, 'UNAVAILABLE'::text])) OR ((working_state_snapshot_id IS NOT NULL) AND (deployed_state_snapshot_id IS NOT NULL) AND (state_identity_contract_version IS NOT NULL) AND (working_state_components IS NOT NULL) AND (deployed_state_components IS NOT NULL))))
);


--
-- Name: deployment_attempts; Type: TABLE; Schema: docs; Owner: -
--

CREATE TABLE docs.deployment_attempts (
    deployment_cycle_id text NOT NULL,
    mode text NOT NULL,
    status text NOT NULL,
    target_page_identity text NOT NULL,
    target_state_identity text NOT NULL,
    target_state_components jsonb NOT NULL,
    state_identity_contract_version text NOT NULL,
    renderer_provenance jsonb,
    publication_provenance jsonb,
    validator_contract_version text,
    release_id text,
    failure_rules text[] DEFAULT '{}'::text[] NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    promoted_at timestamp with time zone,
    completed_at timestamp with time zone,
    validated_manifest_identity text,
    sealed_digest text,
    validation_policy_identity text,
    validation_policy_components jsonb,
    CONSTRAINT deployment_attempts_completed_has_policy CHECK (((status <> 'COMPLETED'::text) OR (validation_policy_identity IS NOT NULL))),
    CONSTRAINT deployment_attempts_completed_has_release CHECK (((status <> 'COMPLETED'::text) OR ((release_id IS NOT NULL) AND (promoted_at IS NOT NULL) AND (completed_at IS NOT NULL)))),
    CONSTRAINT deployment_attempts_completed_is_sealed CHECK (((status <> 'COMPLETED'::text) OR ((validated_manifest_identity IS NOT NULL) AND (sealed_digest IS NOT NULL)))),
    CONSTRAINT deployment_attempts_cycle_is_uuid CHECK ((deployment_cycle_id ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'::text)),
    CONSTRAINT deployment_attempts_failed_has_rules CHECK (((status <> ALL (ARRAY['FAILED'::text, 'RECONCILIATION_REQUIRED'::text])) OR (COALESCE(array_length(failure_rules, 1), 0) >= 1))),
    CONSTRAINT deployment_attempts_mode_valid CHECK ((mode = ANY (ARRAY['CATCH_UP'::text, 'REDEPLOY'::text, 'BOOTSTRAP'::text, 'RECERTIFY_POLICY'::text]))),
    CONSTRAINT deployment_attempts_promoted_has_release CHECK (((status <> ALL (ARRAY['PROMOTED'::text, 'COMPLETED'::text])) OR (release_id IS NOT NULL))),
    CONSTRAINT deployment_attempts_status_valid CHECK ((status = ANY (ARRAY['PINNED'::text, 'BUILDING'::text, 'VALIDATED'::text, 'PROMOTED'::text, 'COMPLETED'::text, 'FAILED'::text, 'RECONCILIATION_REQUIRED'::text]))),
    CONSTRAINT deployment_attempts_target_names_all_components CHECK ((target_state_components ?& ARRAY['page_corpus_identity'::text, 'redirect_register_identity'::text, 'navigation_state_identity'::text]))
);


--
-- Name: page_metadata_history; Type: TABLE; Schema: docs; Owner: -
--

CREATE TABLE docs.page_metadata_history (
    history_id bigint NOT NULL,
    page_resource_id uuid NOT NULL,
    metadata_version bigint NOT NULL,
    workspace_id uuid NOT NULL,
    publication_state text NOT NULL,
    knowledge_class text,
    verification_state text NOT NULL,
    owner_principal_id uuid,
    review_due_at timestamp with time zone,
    criticality text NOT NULL,
    metadata_review_required boolean NOT NULL,
    changed_by_principal_id uuid NOT NULL,
    change_reason text NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT page_metadata_history_reason_nonempty CHECK ((btrim(change_reason) <> ''::text)),
    CONSTRAINT page_metadata_history_version_positive CHECK ((metadata_version >= 1))
);


--
-- Name: page_metadata_history_history_id_seq; Type: SEQUENCE; Schema: docs; Owner: -
--

CREATE SEQUENCE docs.page_metadata_history_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: page_metadata_history_history_id_seq; Type: SEQUENCE OWNED BY; Schema: docs; Owner: -
--

ALTER SEQUENCE docs.page_metadata_history_history_id_seq OWNED BY docs.page_metadata_history.history_id;


--
-- Name: page_verifications; Type: TABLE; Schema: docs; Owner: -
--

CREATE TABLE docs.page_verifications (
    verification_id uuid DEFAULT gen_random_uuid() NOT NULL,
    page_resource_id uuid NOT NULL,
    page_revision text NOT NULL,
    verifier_principal_id uuid NOT NULL,
    verification_state text NOT NULL,
    verified_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT page_verifications_expiry_after_verification CHECK (((expires_at IS NULL) OR (expires_at > verified_at))),
    CONSTRAINT page_verifications_state_valid CHECK ((verification_state = ANY (ARRAY['VERIFIED'::text, 'EXPIRED'::text, 'OUTDATED'::text, 'REVOKED'::text])))
);


--
-- Name: page_versions; Type: TABLE; Schema: docs; Owner: -
--

CREATE TABLE docs.page_versions (
    id integer NOT NULL,
    path text NOT NULL,
    content text NOT NULL,
    revision text NOT NULL,
    updated_by text,
    title text,
    nav_path text,
    version integer,
    archived_at timestamp without time zone DEFAULT now() NOT NULL,
    resource_id uuid
);


--
-- Name: page_versions_id_seq; Type: SEQUENCE; Schema: docs; Owner: -
--

CREATE SEQUENCE docs.page_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: page_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: docs; Owner: -
--

ALTER SEQUENCE docs.page_versions_id_seq OWNED BY docs.page_versions.id;


--
-- Name: pages; Type: TABLE; Schema: docs; Owner: -
--

CREATE TABLE docs.pages (
    id integer NOT NULL,
    path text NOT NULL,
    title text NOT NULL,
    nav_path text NOT NULL,
    content text NOT NULL,
    revision text DEFAULT (gen_random_uuid())::text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_by text DEFAULT 'unknown'::text NOT NULL,
    resource_id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    publication_state text NOT NULL,
    knowledge_class text,
    verification_state text DEFAULT 'UNVERIFIED'::text NOT NULL,
    owner_principal_id uuid,
    review_due_at timestamp with time zone,
    criticality text DEFAULT 'NORMAL'::text NOT NULL,
    metadata_review_required boolean DEFAULT true NOT NULL,
    metadata_version bigint DEFAULT 1 NOT NULL,
    CONSTRAINT chk_content_size CHECK ((length(content) < 1000000)),
    CONSTRAINT chk_nav_path CHECK (((nav_path = 'Home'::text) OR (nav_path ~ '^[^/]+(/[^/]+)+$'::text))),
    CONSTRAINT chk_status CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'inactive'::text, 'archived'::text]))),
    CONSTRAINT chk_valid_path CHECK ((path ~ '^[a-z0-9/_-]+\.md$'::text)),
    CONSTRAINT docs_pages_criticality_valid CHECK ((criticality = ANY (ARRAY['NORMAL'::text, 'IMPORTANT'::text, 'OPERATIONAL_CRITICAL'::text, 'POLICY_REQUIRED'::text]))),
    CONSTRAINT docs_pages_knowledge_class_valid CHECK (((knowledge_class IS NULL) OR (knowledge_class = ANY (ARRAY['REFERENCE'::text, 'OPERATION'::text, 'ARCHITECTURE'::text, 'DESIGN'::text, 'DECISION'::text, 'POLICY'::text, 'EVIDENCE'::text, 'WORK_NOTE'::text])))),
    CONSTRAINT docs_pages_metadata_version_positive CHECK ((metadata_version >= 1)),
    CONSTRAINT docs_pages_publication_state_valid CHECK ((publication_state = ANY (ARRAY['DRAFT'::text, 'PUBLISHED'::text, 'ARCHIVED'::text]))),
    CONSTRAINT docs_pages_verification_state_valid CHECK ((verification_state = ANY (ARRAY['UNVERIFIED'::text, 'VERIFIED'::text, 'EXPIRED'::text, 'OUTDATED'::text])))
);


--
-- Name: COLUMN pages.metadata_review_required; Type: COMMENT; Schema: docs; Owner: -
--

COMMENT ON COLUMN docs.pages.metadata_review_required IS 'True until imported legacy metadata is classified and reviewed in the new orthogonal model.';


--
-- Name: pages_id_seq; Type: SEQUENCE; Schema: docs; Owner: -
--

CREATE SEQUENCE docs.pages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pages_id_seq; Type: SEQUENCE OWNED BY; Schema: docs; Owner: -
--

ALTER SEQUENCE docs.pages_id_seq OWNED BY docs.pages.id;


--
-- Name: redirects; Type: TABLE; Schema: docs; Owner: -
--

CREATE TABLE docs.redirects (
    from_path text NOT NULL,
    to_path text NOT NULL,
    revision text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by text DEFAULT 'api'::text NOT NULL,
    CONSTRAINT redirects_from_path_canonical CHECK ((from_path ~ '^[a-z0-9/_-]+\.md$'::text)),
    CONSTRAINT redirects_revision_is_uuid CHECK ((revision ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'::text)),
    CONSTRAINT redirects_source_differs_from_target CHECK ((from_path <> to_path)),
    CONSTRAINT redirects_to_path_canonical CHECK ((to_path ~ '^[a-z0-9/_-]+\.md$'::text)),
    CONSTRAINT redirects_version_positive CHECK ((version >= 1))
);


--
-- Name: reorganisation_operations; Type: TABLE; Schema: docs; Owner: -
--

CREATE TABLE docs.reorganisation_operations (
    operation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    plan_id uuid NOT NULL,
    sequence integer NOT NULL,
    idempotency_key text NOT NULL,
    operation_type text NOT NULL,
    page_resource_id uuid,
    expected_revision text,
    payload jsonb NOT NULL,
    analysis_result jsonb,
    inverse_operation jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reorganisation_operation_idempotency_nonempty CHECK ((btrim(idempotency_key) <> ''::text)),
    CONSTRAINT reorganisation_operation_page_binding CHECK (((operation_type = ANY (ARRAY['REORDER_SECTIONS'::text, 'ADD_REDIRECT'::text, 'REMOVE_REDIRECT'::text])) OR ((page_resource_id IS NOT NULL) AND (expected_revision IS NOT NULL)))),
    CONSTRAINT reorganisation_operation_sequence_nonnegative CHECK ((sequence >= 0)),
    CONSTRAINT reorganisation_operation_type_valid CHECK ((operation_type = ANY (ARRAY['MOVE_PAGE'::text, 'REPARENT_NAV'::text, 'REORDER_SECTIONS'::text, 'ADD_REDIRECT'::text, 'REMOVE_REDIRECT'::text, 'ARCHIVE_PAGE'::text, 'RESTORE_PAGE'::text])))
);


--
-- Name: TABLE reorganisation_operations; Type: COMMENT; Schema: docs; Owner: -
--

COMMENT ON TABLE docs.reorganisation_operations IS 'Ordered structural operations with exact revision bindings and generated inverse operations.';


--
-- Name: reorganisation_plans; Type: TABLE; Schema: docs; Owner: -
--

CREATE TABLE docs.reorganisation_plans (
    plan_id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_key text NOT NULL,
    title text NOT NULL,
    purpose text NOT NULL,
    author_principal_id uuid NOT NULL,
    status text DEFAULT 'DRAFT'::text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    idempotency_key text NOT NULL,
    base_state_identity text,
    analysis_receipt jsonb,
    validation_receipt jsonb,
    linked_change_id uuid,
    execution_receipt jsonb,
    certification_receipt jsonb,
    stabilization_receipt jsonb,
    compensating_plan_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reorganisation_plan_idempotency_nonempty CHECK ((btrim(idempotency_key) <> ''::text)),
    CONSTRAINT reorganisation_plan_purpose_nonempty CHECK ((btrim(purpose) <> ''::text)),
    CONSTRAINT reorganisation_plan_status_valid CHECK ((status = ANY (ARRAY['DRAFT'::text, 'ANALYZED'::text, 'VALIDATED'::text, 'SUBMITTED'::text, 'APPROVED'::text, 'EXECUTING'::text, 'CERTIFYING'::text, 'STABILIZING'::text, 'COMPLETED'::text, 'FAILED'::text, 'COMPENSATION_REQUIRED'::text, 'CANCELLED'::text]))),
    CONSTRAINT reorganisation_plan_title_nonempty CHECK ((btrim(title) <> ''::text)),
    CONSTRAINT reorganisation_plan_version_positive CHECK ((version >= 1)),
    CONSTRAINT reorganisation_plan_workspace_key_valid CHECK ((workspace_key ~ '^[a-z0-9][a-z0-9_-]{0,62}$'::text))
);


--
-- Name: TABLE reorganisation_plans; Type: COMMENT; Schema: docs; Owner: -
--

COMMENT ON TABLE docs.reorganisation_plans IS 'Governed structural-change plans. Analysis and approval do not mutate authored state.';


--
-- Name: reorganisation_reviews; Type: TABLE; Schema: docs; Owner: -
--

CREATE TABLE docs.reorganisation_reviews (
    review_id uuid DEFAULT gen_random_uuid() NOT NULL,
    plan_id uuid NOT NULL,
    reviewer_principal_id uuid NOT NULL,
    decision text NOT NULL,
    note text,
    reviewed_plan_version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reorganisation_review_decision_valid CHECK ((decision = ANY (ARRAY['APPROVE'::text, 'REQUEST_CHANGES'::text, 'COMMENT'::text]))),
    CONSTRAINT reorganisation_review_version_positive CHECK ((reviewed_plan_version >= 1))
);


--
-- Name: sections; Type: TABLE; Schema: docs; Owner: -
--

CREATE TABLE docs.sections (
    name text NOT NULL,
    sort_order integer NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_by text DEFAULT 'unknown'::text NOT NULL,
    CONSTRAINT chk_section_name CHECK (((name = btrim(name)) AND (name !~ '/'::text) AND (name <> 'Home'::text)))
);


--
-- Name: trust_mutation_receipts; Type: TABLE; Schema: docs; Owner: -
--

CREATE TABLE docs.trust_mutation_receipts (
    principal_id uuid NOT NULL,
    idempotency_key text NOT NULL,
    operation_type text NOT NULL,
    page_resource_id uuid NOT NULL,
    request_hash text NOT NULL,
    response jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_receipts_hash_sha256 CHECK ((request_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT trust_receipts_key_nonempty CHECK ((btrim(idempotency_key) <> ''::text)),
    CONSTRAINT trust_receipts_operation_nonempty CHECK ((btrim(operation_type) <> ''::text))
);


--
-- Name: initiative_activities; Type: TABLE; Schema: work; Owner: -
--

CREATE TABLE work.initiative_activities (
    activity_id uuid DEFAULT gen_random_uuid() NOT NULL,
    initiative_id uuid NOT NULL,
    author_principal_id uuid NOT NULL,
    idempotency_key text NOT NULL,
    activity_type text NOT NULL,
    body text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT initiative_activities_body_nonempty CHECK ((btrim(body) <> ''::text)),
    CONSTRAINT initiative_activities_idempotency_nonempty CHECK ((btrim(idempotency_key) <> ''::text)),
    CONSTRAINT initiative_activities_type_valid CHECK ((activity_type = ANY (ARRAY['NOTE'::text, 'OBSERVATION'::text, 'DECISION_REQUIRED'::text, 'HANDOFF'::text, 'SOAK_OBSERVATION'::text, 'BLOCKER'::text, 'RESOLUTION'::text])))
);


--
-- Name: initiative_dependencies; Type: TABLE; Schema: work; Owner: -
--

CREATE TABLE work.initiative_dependencies (
    initiative_id uuid NOT NULL,
    depends_on_initiative_id uuid NOT NULL,
    dependency_kind text DEFAULT 'REQUIRES'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT initiative_dependencies_kind_valid CHECK ((dependency_kind = ANY (ARRAY['REQUIRES'::text, 'BLOCKED_BY'::text, 'RELATED'::text]))),
    CONSTRAINT initiative_dependencies_no_self CHECK ((initiative_id <> depends_on_initiative_id))
);


--
-- Name: initiative_links; Type: TABLE; Schema: work; Owner: -
--

CREATE TABLE work.initiative_links (
    initiative_id uuid NOT NULL,
    relation text NOT NULL,
    resource_type text NOT NULL,
    resource_id text NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT initiative_links_relation_valid CHECK ((relation = ANY (ARRAY['CONTEXT'::text, 'EVIDENCE'::text, 'DECISION'::text, 'RUNBOOK'::text, 'BLOCKED_BY'::text, 'PROMOTES_TO'::text, 'CHANGE_PROPOSAL'::text, 'CATALOG_SNAPSHOT'::text]))),
    CONSTRAINT initiative_links_resource_nonempty CHECK ((btrim(resource_id) <> ''::text)),
    CONSTRAINT initiative_links_resource_type_valid CHECK ((resource_type = ANY (ARRAY['PAGE'::text, 'INITIATIVE'::text, 'CHANGE'::text, 'CATALOG'::text, 'EXTERNAL'::text])))
);


--
-- Name: initiatives; Type: TABLE; Schema: work; Owner: -
--

CREATE TABLE work.initiatives (
    initiative_id uuid DEFAULT gen_random_uuid() NOT NULL,
    initiative_key text NOT NULL,
    workspace_id uuid NOT NULL,
    title text NOT NULL,
    objective text NOT NULL,
    scope text,
    owner_principal_id uuid NOT NULL,
    idempotency_key text NOT NULL,
    work_state text DEFAULT 'BACKLOG'::text NOT NULL,
    priority text DEFAULT 'NORMAL'::text NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    target_date date,
    review_due_at timestamp with time zone,
    blocker_summary text,
    soak_started_at timestamp with time zone,
    soak_review_at timestamp with time zone,
    soak_success_criteria text,
    soak_failure_conditions text,
    parked_reason text,
    parked_review_at timestamp with time zone,
    parked_indefinitely boolean DEFAULT false NOT NULL,
    promotion_state text DEFAULT 'NOT_READY'::text NOT NULL,
    promotion_change_id uuid,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT initiatives_complete_timestamp CHECK (((work_state <> ALL (ARRAY['COMPLETE'::text, 'ABANDONED'::text])) OR (completed_at IS NOT NULL))),
    CONSTRAINT initiatives_idempotency_nonempty CHECK ((btrim(idempotency_key) <> ''::text)),
    CONSTRAINT initiatives_key_valid CHECK ((initiative_key ~ '^[a-z0-9][a-z0-9_-]{0,95}$'::text)),
    CONSTRAINT initiatives_objective_nonempty CHECK ((btrim(objective) <> ''::text)),
    CONSTRAINT initiatives_parked_contract CHECK (((work_state <> 'PARKED'::text) OR ((btrim(COALESCE(parked_reason, ''::text)) <> ''::text) AND ((parked_review_at IS NOT NULL) OR parked_indefinitely)))),
    CONSTRAINT initiatives_priority_valid CHECK ((priority = ANY (ARRAY['LOW'::text, 'NORMAL'::text, 'HIGH'::text, 'CRITICAL'::text]))),
    CONSTRAINT initiatives_promoted_has_change CHECK (((promotion_state <> 'PROMOTED'::text) OR (promotion_change_id IS NOT NULL))),
    CONSTRAINT initiatives_promotion_state_valid CHECK ((promotion_state = ANY (ARRAY['NOT_READY'::text, 'READY'::text, 'IN_PROGRESS'::text, 'PROMOTED'::text, 'NOT_REQUIRED'::text]))),
    CONSTRAINT initiatives_soaking_contract CHECK (((work_state <> 'SOAKING'::text) OR ((soak_started_at IS NOT NULL) AND (soak_review_at IS NOT NULL) AND (btrim(COALESCE(soak_success_criteria, ''::text)) <> ''::text) AND (btrim(COALESCE(soak_failure_conditions, ''::text)) <> ''::text)))),
    CONSTRAINT initiatives_title_nonempty CHECK ((btrim(title) <> ''::text)),
    CONSTRAINT initiatives_version_positive CHECK ((version >= 1)),
    CONSTRAINT initiatives_work_state_valid CHECK ((work_state = ANY (ARRAY['BACKLOG'::text, 'ACTIVE'::text, 'BLOCKED'::text, 'SOAKING'::text, 'PAUSED'::text, 'PARKED'::text, 'COMPLETE'::text, 'ABANDONED'::text])))
);


--
-- Name: TABLE initiatives; Type: COMMENT; Schema: work; Owner: -
--

COMMENT ON TABLE work.initiatives IS 'First-class active work; never treated as durable certified knowledge without explicit promotion.';


--
-- Name: mutation_receipts; Type: TABLE; Schema: work; Owner: -
--

CREATE TABLE work.mutation_receipts (
    principal_id uuid NOT NULL,
    idempotency_key text NOT NULL,
    operation_type text NOT NULL,
    resource_id text,
    request_hash text NOT NULL,
    response jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT work_receipts_hash_sha256 CHECK ((request_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT work_receipts_key_nonempty CHECK ((btrim(idempotency_key) <> ''::text)),
    CONSTRAINT work_receipts_operation_nonempty CHECK ((btrim(operation_type) <> ''::text))
);


--
-- Name: events event_seq; Type: DEFAULT; Schema: docplane; Owner: -
--

ALTER TABLE ONLY docplane.events ALTER COLUMN event_seq SET DEFAULT nextval('docplane.events_event_seq_seq'::regclass);


--
-- Name: page_metadata_history history_id; Type: DEFAULT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.page_metadata_history ALTER COLUMN history_id SET DEFAULT nextval('docs.page_metadata_history_history_id_seq'::regclass);


--
-- Name: page_versions id; Type: DEFAULT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.page_versions ALTER COLUMN id SET DEFAULT nextval('docs.page_versions_id_seq'::regclass);


--
-- Name: pages id; Type: DEFAULT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.pages ALTER COLUMN id SET DEFAULT nextval('docs.pages_id_seq'::regclass);


--
-- Name: api_tokens api_tokens_pkey; Type: CONSTRAINT; Schema: docplane; Owner: -
--

ALTER TABLE ONLY docplane.api_tokens
    ADD CONSTRAINT api_tokens_pkey PRIMARY KEY (token_id);


--
-- Name: api_tokens api_tokens_token_hash_key; Type: CONSTRAINT; Schema: docplane; Owner: -
--

ALTER TABLE ONLY docplane.api_tokens
    ADD CONSTRAINT api_tokens_token_hash_key UNIQUE (token_hash);


--
-- Name: events events_event_id_key; Type: CONSTRAINT; Schema: docplane; Owner: -
--

ALTER TABLE ONLY docplane.events
    ADD CONSTRAINT events_event_id_key UNIQUE (event_id);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: docplane; Owner: -
--

ALTER TABLE ONLY docplane.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (event_seq);


--
-- Name: events events_producer_id_idempotency_key_key; Type: CONSTRAINT; Schema: docplane; Owner: -
--

ALTER TABLE ONLY docplane.events
    ADD CONSTRAINT events_producer_id_idempotency_key_key UNIQUE (producer_id, idempotency_key);


--
-- Name: principals principals_pkey; Type: CONSTRAINT; Schema: docplane; Owner: -
--

ALTER TABLE ONLY docplane.principals
    ADD CONSTRAINT principals_pkey PRIMARY KEY (principal_id);


--
-- Name: workspace_memberships workspace_memberships_pkey; Type: CONSTRAINT; Schema: docplane; Owner: -
--

ALTER TABLE ONLY docplane.workspace_memberships
    ADD CONSTRAINT workspace_memberships_pkey PRIMARY KEY (workspace_id, principal_id);


--
-- Name: workspaces workspaces_pkey; Type: CONSTRAINT; Schema: docplane; Owner: -
--

ALTER TABLE ONLY docplane.workspaces
    ADD CONSTRAINT workspaces_pkey PRIMARY KEY (workspace_id);


--
-- Name: workspaces workspaces_workspace_key_key; Type: CONSTRAINT; Schema: docplane; Owner: -
--

ALTER TABLE ONLY docplane.workspaces
    ADD CONSTRAINT workspaces_workspace_key_key UNIQUE (workspace_key);


--
-- Name: change_operations change_operations_change_id_idempotency_key_key; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.change_operations
    ADD CONSTRAINT change_operations_change_id_idempotency_key_key UNIQUE (change_id, idempotency_key);


--
-- Name: change_operations change_operations_change_id_sequence_key; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.change_operations
    ADD CONSTRAINT change_operations_change_id_sequence_key UNIQUE (change_id, sequence);


--
-- Name: change_operations change_operations_pkey; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.change_operations
    ADD CONSTRAINT change_operations_pkey PRIMARY KEY (operation_id);


--
-- Name: change_proposals change_proposals_author_principal_id_idempotency_key_key; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.change_proposals
    ADD CONSTRAINT change_proposals_author_principal_id_idempotency_key_key UNIQUE (author_principal_id, idempotency_key);


--
-- Name: change_proposals change_proposals_pkey; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.change_proposals
    ADD CONSTRAINT change_proposals_pkey PRIMARY KEY (change_id);


--
-- Name: corpus_certification corpus_certification_pkey; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.corpus_certification
    ADD CONSTRAINT corpus_certification_pkey PRIMARY KEY (id);


--
-- Name: deployment_attempts deployment_attempts_pkey; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.deployment_attempts
    ADD CONSTRAINT deployment_attempts_pkey PRIMARY KEY (deployment_cycle_id);


--
-- Name: page_metadata_history page_metadata_history_pkey; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.page_metadata_history
    ADD CONSTRAINT page_metadata_history_pkey PRIMARY KEY (history_id);


--
-- Name: page_verifications page_verifications_pkey; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.page_verifications
    ADD CONSTRAINT page_verifications_pkey PRIMARY KEY (verification_id);


--
-- Name: page_versions page_versions_pkey; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.page_versions
    ADD CONSTRAINT page_versions_pkey PRIMARY KEY (id);


--
-- Name: pages pages_path_key; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.pages
    ADD CONSTRAINT pages_path_key UNIQUE (path);


--
-- Name: pages pages_pkey; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.pages
    ADD CONSTRAINT pages_pkey PRIMARY KEY (id);


--
-- Name: redirects redirects_pkey; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.redirects
    ADD CONSTRAINT redirects_pkey PRIMARY KEY (from_path);


--
-- Name: reorganisation_operations reorganisation_operations_pkey; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.reorganisation_operations
    ADD CONSTRAINT reorganisation_operations_pkey PRIMARY KEY (operation_id);


--
-- Name: reorganisation_operations reorganisation_operations_plan_id_idempotency_key_key; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.reorganisation_operations
    ADD CONSTRAINT reorganisation_operations_plan_id_idempotency_key_key UNIQUE (plan_id, idempotency_key);


--
-- Name: reorganisation_operations reorganisation_operations_plan_id_sequence_key; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.reorganisation_operations
    ADD CONSTRAINT reorganisation_operations_plan_id_sequence_key UNIQUE (plan_id, sequence);


--
-- Name: reorganisation_plans reorganisation_plans_author_principal_id_idempotency_key_key; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.reorganisation_plans
    ADD CONSTRAINT reorganisation_plans_author_principal_id_idempotency_key_key UNIQUE (author_principal_id, idempotency_key);


--
-- Name: reorganisation_plans reorganisation_plans_pkey; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.reorganisation_plans
    ADD CONSTRAINT reorganisation_plans_pkey PRIMARY KEY (plan_id);


--
-- Name: reorganisation_reviews reorganisation_reviews_pkey; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.reorganisation_reviews
    ADD CONSTRAINT reorganisation_reviews_pkey PRIMARY KEY (review_id);


--
-- Name: sections sections_pkey; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.sections
    ADD CONSTRAINT sections_pkey PRIMARY KEY (name);


--
-- Name: trust_mutation_receipts trust_mutation_receipts_pkey; Type: CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.trust_mutation_receipts
    ADD CONSTRAINT trust_mutation_receipts_pkey PRIMARY KEY (principal_id, idempotency_key);


--
-- Name: initiative_activities initiative_activities_initiative_id_author_principal_id_ide_key; Type: CONSTRAINT; Schema: work; Owner: -
--

ALTER TABLE ONLY work.initiative_activities
    ADD CONSTRAINT initiative_activities_initiative_id_author_principal_id_ide_key UNIQUE (initiative_id, author_principal_id, idempotency_key);


--
-- Name: initiative_activities initiative_activities_pkey; Type: CONSTRAINT; Schema: work; Owner: -
--

ALTER TABLE ONLY work.initiative_activities
    ADD CONSTRAINT initiative_activities_pkey PRIMARY KEY (activity_id);


--
-- Name: initiative_dependencies initiative_dependencies_pkey; Type: CONSTRAINT; Schema: work; Owner: -
--

ALTER TABLE ONLY work.initiative_dependencies
    ADD CONSTRAINT initiative_dependencies_pkey PRIMARY KEY (initiative_id, depends_on_initiative_id);


--
-- Name: initiative_links initiative_links_pkey; Type: CONSTRAINT; Schema: work; Owner: -
--

ALTER TABLE ONLY work.initiative_links
    ADD CONSTRAINT initiative_links_pkey PRIMARY KEY (initiative_id, relation, resource_type, resource_id);


--
-- Name: initiatives initiatives_owner_principal_id_idempotency_key_key; Type: CONSTRAINT; Schema: work; Owner: -
--

ALTER TABLE ONLY work.initiatives
    ADD CONSTRAINT initiatives_owner_principal_id_idempotency_key_key UNIQUE (owner_principal_id, idempotency_key);


--
-- Name: initiatives initiatives_pkey; Type: CONSTRAINT; Schema: work; Owner: -
--

ALTER TABLE ONLY work.initiatives
    ADD CONSTRAINT initiatives_pkey PRIMARY KEY (initiative_id);


--
-- Name: initiatives initiatives_workspace_id_initiative_key_key; Type: CONSTRAINT; Schema: work; Owner: -
--

ALTER TABLE ONLY work.initiatives
    ADD CONSTRAINT initiatives_workspace_id_initiative_key_key UNIQUE (workspace_id, initiative_key);


--
-- Name: mutation_receipts mutation_receipts_pkey; Type: CONSTRAINT; Schema: work; Owner: -
--

ALTER TABLE ONLY work.mutation_receipts
    ADD CONSTRAINT mutation_receipts_pkey PRIMARY KEY (principal_id, idempotency_key);


--
-- Name: api_tokens_principal_idx; Type: INDEX; Schema: docplane; Owner: -
--

CREATE INDEX api_tokens_principal_idx ON docplane.api_tokens USING btree (principal_id) WHERE (revoked_at IS NULL);


--
-- Name: events_occurred_idx; Type: INDEX; Schema: docplane; Owner: -
--

CREATE INDEX events_occurred_idx ON docplane.events USING btree (occurred_at DESC, event_seq DESC);


--
-- Name: events_page_idx; Type: INDEX; Schema: docplane; Owner: -
--

CREATE INDEX events_page_idx ON docplane.events USING btree (page_resource_id, occurred_at DESC) WHERE (page_resource_id IS NOT NULL);


--
-- Name: events_principal_idx; Type: INDEX; Schema: docplane; Owner: -
--

CREATE INDEX events_principal_idx ON docplane.events USING btree (principal_id, occurred_at DESC) WHERE (principal_id IS NOT NULL);


--
-- Name: events_type_idx; Type: INDEX; Schema: docplane; Owner: -
--

CREATE INDEX events_type_idx ON docplane.events USING btree (event_type, occurred_at DESC);


--
-- Name: events_workspace_idx; Type: INDEX; Schema: docplane; Owner: -
--

CREATE INDEX events_workspace_idx ON docplane.events USING btree (workspace_key, occurred_at DESC) WHERE (workspace_key IS NOT NULL);


--
-- Name: change_operations_page_idx; Type: INDEX; Schema: docs; Owner: -
--

CREATE INDEX change_operations_page_idx ON docs.change_operations USING btree (page_resource_id);


--
-- Name: change_proposals_status_idx; Type: INDEX; Schema: docs; Owner: -
--

CREATE INDEX change_proposals_status_idx ON docs.change_proposals USING btree (status, updated_at DESC);


--
-- Name: deployment_attempts_one_active; Type: INDEX; Schema: docs; Owner: -
--

CREATE UNIQUE INDEX deployment_attempts_one_active ON docs.deployment_attempts USING btree ((true)) WHERE (status = ANY (ARRAY['PINNED'::text, 'BUILDING'::text, 'VALIDATED'::text, 'PROMOTED'::text, 'RECONCILIATION_REQUIRED'::text]));


--
-- Name: deployment_attempts_started_idx; Type: INDEX; Schema: docs; Owner: -
--

CREATE INDEX deployment_attempts_started_idx ON docs.deployment_attempts USING btree (started_at DESC);


--
-- Name: docs_page_versions_path_idx; Type: INDEX; Schema: docs; Owner: -
--

CREATE INDEX docs_page_versions_path_idx ON docs.page_versions USING btree (path);


--
-- Name: docs_page_versions_resource_id_idx; Type: INDEX; Schema: docs; Owner: -
--

CREATE INDEX docs_page_versions_resource_id_idx ON docs.page_versions USING btree (resource_id, archived_at DESC);


--
-- Name: docs_pages_owner_idx; Type: INDEX; Schema: docs; Owner: -
--

CREATE INDEX docs_pages_owner_idx ON docs.pages USING btree (owner_principal_id) WHERE (owner_principal_id IS NOT NULL);


--
-- Name: docs_pages_path_idx; Type: INDEX; Schema: docs; Owner: -
--

CREATE INDEX docs_pages_path_idx ON docs.pages USING btree (path);


--
-- Name: docs_pages_resource_id_uidx; Type: INDEX; Schema: docs; Owner: -
--

CREATE UNIQUE INDEX docs_pages_resource_id_uidx ON docs.pages USING btree (resource_id);


--
-- Name: docs_pages_verification_due_idx; Type: INDEX; Schema: docs; Owner: -
--

CREATE INDEX docs_pages_verification_due_idx ON docs.pages USING btree (verification_state, review_due_at) WHERE (publication_state = 'PUBLISHED'::text);


--
-- Name: docs_pages_workspace_idx; Type: INDEX; Schema: docs; Owner: -
--

CREATE INDEX docs_pages_workspace_idx ON docs.pages USING btree (workspace_id, publication_state, knowledge_class);


--
-- Name: docs_sections_order_idx; Type: INDEX; Schema: docs; Owner: -
--

CREATE INDEX docs_sections_order_idx ON docs.sections USING btree (sort_order);


--
-- Name: page_metadata_history_page_idx; Type: INDEX; Schema: docs; Owner: -
--

CREATE INDEX page_metadata_history_page_idx ON docs.page_metadata_history USING btree (page_resource_id, metadata_version DESC);


--
-- Name: page_verifications_one_current; Type: INDEX; Schema: docs; Owner: -
--

CREATE UNIQUE INDEX page_verifications_one_current ON docs.page_verifications USING btree (page_resource_id) WHERE (verification_state = 'VERIFIED'::text);


--
-- Name: page_verifications_page_idx; Type: INDEX; Schema: docs; Owner: -
--

CREATE INDEX page_verifications_page_idx ON docs.page_verifications USING btree (page_resource_id, verified_at DESC);


--
-- Name: redirects_to_path_idx; Type: INDEX; Schema: docs; Owner: -
--

CREATE INDEX redirects_to_path_idx ON docs.redirects USING btree (to_path);


--
-- Name: reorganisation_operations_plan_idx; Type: INDEX; Schema: docs; Owner: -
--

CREATE INDEX reorganisation_operations_plan_idx ON docs.reorganisation_operations USING btree (plan_id, sequence);


--
-- Name: reorganisation_plans_workspace_status_idx; Type: INDEX; Schema: docs; Owner: -
--

CREATE INDEX reorganisation_plans_workspace_status_idx ON docs.reorganisation_plans USING btree (workspace_key, status, updated_at DESC);


--
-- Name: reorganisation_reviews_plan_idx; Type: INDEX; Schema: docs; Owner: -
--

CREATE INDEX reorganisation_reviews_plan_idx ON docs.reorganisation_reviews USING btree (plan_id, created_at DESC);


--
-- Name: trust_mutation_receipts_page_idx; Type: INDEX; Schema: docs; Owner: -
--

CREATE INDEX trust_mutation_receipts_page_idx ON docs.trust_mutation_receipts USING btree (page_resource_id, created_at DESC);


--
-- Name: initiative_activities_timeline_idx; Type: INDEX; Schema: work; Owner: -
--

CREATE INDEX initiative_activities_timeline_idx ON work.initiative_activities USING btree (initiative_id, created_at DESC);


--
-- Name: initiatives_owner_idx; Type: INDEX; Schema: work; Owner: -
--

CREATE INDEX initiatives_owner_idx ON work.initiatives USING btree (owner_principal_id, work_state);


--
-- Name: initiatives_parked_due_idx; Type: INDEX; Schema: work; Owner: -
--

CREATE INDEX initiatives_parked_due_idx ON work.initiatives USING btree (parked_review_at) WHERE ((work_state = 'PARKED'::text) AND (parked_indefinitely = false));


--
-- Name: initiatives_soak_due_idx; Type: INDEX; Schema: work; Owner: -
--

CREATE INDEX initiatives_soak_due_idx ON work.initiatives USING btree (soak_review_at) WHERE (work_state = 'SOAKING'::text);


--
-- Name: initiatives_state_idx; Type: INDEX; Schema: work; Owner: -
--

CREATE INDEX initiatives_state_idx ON work.initiatives USING btree (work_state, review_due_at, updated_at DESC);


--
-- Name: work_mutation_receipts_resource_idx; Type: INDEX; Schema: work; Owner: -
--

CREATE INDEX work_mutation_receipts_resource_idx ON work.mutation_receipts USING btree (resource_id, created_at DESC) WHERE (resource_id IS NOT NULL);


--
-- Name: principals principals_touch_updated_at; Type: TRIGGER; Schema: docplane; Owner: -
--

CREATE TRIGGER principals_touch_updated_at BEFORE UPDATE ON docplane.principals FOR EACH ROW EXECUTE FUNCTION docplane.touch_updated_at();


--
-- Name: change_proposals change_proposals_touch_updated_at; Type: TRIGGER; Schema: docs; Owner: -
--

CREATE TRIGGER change_proposals_touch_updated_at BEFORE UPDATE ON docs.change_proposals FOR EACH ROW EXECUTE FUNCTION docplane.touch_updated_at();


--
-- Name: corpus_certification corpus_certification_monotonic_trg; Type: TRIGGER; Schema: docs; Owner: -
--

CREATE TRIGGER corpus_certification_monotonic_trg BEFORE UPDATE ON docs.corpus_certification FOR EACH ROW EXECUTE FUNCTION docs.corpus_certification_monotonic();


--
-- Name: pages docs_pages_default_workspace; Type: TRIGGER; Schema: docs; Owner: -
--

CREATE TRIGGER docs_pages_default_workspace BEFORE INSERT ON docs.pages FOR EACH ROW EXECUTE FUNCTION docplane.default_page_workspace();


--
-- Name: pages docs_pages_invalidate_verification; Type: TRIGGER; Schema: docs; Owner: -
--

CREATE TRIGGER docs_pages_invalidate_verification BEFORE UPDATE OF revision ON docs.pages FOR EACH ROW EXECUTE FUNCTION docs.invalidate_verification_on_revision_change();


--
-- Name: redirects redirects_monotonic_trg; Type: TRIGGER; Schema: docs; Owner: -
--

CREATE TRIGGER redirects_monotonic_trg BEFORE UPDATE ON docs.redirects FOR EACH ROW EXECUTE FUNCTION docs.redirects_monotonic();


--
-- Name: initiatives initiatives_require_work_workspace; Type: TRIGGER; Schema: work; Owner: -
--

CREATE TRIGGER initiatives_require_work_workspace BEFORE INSERT OR UPDATE ON work.initiatives FOR EACH ROW EXECUTE FUNCTION work.require_work_workspace();


--
-- Name: api_tokens api_tokens_principal_id_fkey; Type: FK CONSTRAINT; Schema: docplane; Owner: -
--

ALTER TABLE ONLY docplane.api_tokens
    ADD CONSTRAINT api_tokens_principal_id_fkey FOREIGN KEY (principal_id) REFERENCES docplane.principals(principal_id);


--
-- Name: events events_principal_id_fkey; Type: FK CONSTRAINT; Schema: docplane; Owner: -
--

ALTER TABLE ONLY docplane.events
    ADD CONSTRAINT events_principal_id_fkey FOREIGN KEY (principal_id) REFERENCES docplane.principals(principal_id);


--
-- Name: principals principals_owner_principal_id_fkey; Type: FK CONSTRAINT; Schema: docplane; Owner: -
--

ALTER TABLE ONLY docplane.principals
    ADD CONSTRAINT principals_owner_principal_id_fkey FOREIGN KEY (owner_principal_id) REFERENCES docplane.principals(principal_id);


--
-- Name: workspace_memberships workspace_memberships_created_by_fkey; Type: FK CONSTRAINT; Schema: docplane; Owner: -
--

ALTER TABLE ONLY docplane.workspace_memberships
    ADD CONSTRAINT workspace_memberships_created_by_fkey FOREIGN KEY (created_by) REFERENCES docplane.principals(principal_id);


--
-- Name: workspace_memberships workspace_memberships_principal_id_fkey; Type: FK CONSTRAINT; Schema: docplane; Owner: -
--

ALTER TABLE ONLY docplane.workspace_memberships
    ADD CONSTRAINT workspace_memberships_principal_id_fkey FOREIGN KEY (principal_id) REFERENCES docplane.principals(principal_id) ON DELETE CASCADE;


--
-- Name: workspace_memberships workspace_memberships_workspace_id_fkey; Type: FK CONSTRAINT; Schema: docplane; Owner: -
--

ALTER TABLE ONLY docplane.workspace_memberships
    ADD CONSTRAINT workspace_memberships_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES docplane.workspaces(workspace_id) ON DELETE CASCADE;


--
-- Name: change_operations change_operations_change_id_fkey; Type: FK CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.change_operations
    ADD CONSTRAINT change_operations_change_id_fkey FOREIGN KEY (change_id) REFERENCES docs.change_proposals(change_id) ON DELETE CASCADE;


--
-- Name: change_proposals change_proposals_author_principal_id_fkey; Type: FK CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.change_proposals
    ADD CONSTRAINT change_proposals_author_principal_id_fkey FOREIGN KEY (author_principal_id) REFERENCES docplane.principals(principal_id);


--
-- Name: page_metadata_history page_metadata_history_changed_by_principal_id_fkey; Type: FK CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.page_metadata_history
    ADD CONSTRAINT page_metadata_history_changed_by_principal_id_fkey FOREIGN KEY (changed_by_principal_id) REFERENCES docplane.principals(principal_id);


--
-- Name: page_verifications page_verifications_verifier_principal_id_fkey; Type: FK CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.page_verifications
    ADD CONSTRAINT page_verifications_verifier_principal_id_fkey FOREIGN KEY (verifier_principal_id) REFERENCES docplane.principals(principal_id);


--
-- Name: pages pages_owner_principal_id_fkey; Type: FK CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.pages
    ADD CONSTRAINT pages_owner_principal_id_fkey FOREIGN KEY (owner_principal_id) REFERENCES docplane.principals(principal_id);


--
-- Name: pages pages_workspace_id_fkey; Type: FK CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.pages
    ADD CONSTRAINT pages_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES docplane.workspaces(workspace_id);


--
-- Name: reorganisation_operations reorganisation_operations_plan_id_fkey; Type: FK CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.reorganisation_operations
    ADD CONSTRAINT reorganisation_operations_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES docs.reorganisation_plans(plan_id) ON DELETE CASCADE;


--
-- Name: reorganisation_plans reorganisation_plans_author_principal_id_fkey; Type: FK CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.reorganisation_plans
    ADD CONSTRAINT reorganisation_plans_author_principal_id_fkey FOREIGN KEY (author_principal_id) REFERENCES docplane.principals(principal_id);


--
-- Name: reorganisation_plans reorganisation_plans_compensating_plan_id_fkey; Type: FK CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.reorganisation_plans
    ADD CONSTRAINT reorganisation_plans_compensating_plan_id_fkey FOREIGN KEY (compensating_plan_id) REFERENCES docs.reorganisation_plans(plan_id);


--
-- Name: reorganisation_plans reorganisation_plans_linked_change_id_fkey; Type: FK CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.reorganisation_plans
    ADD CONSTRAINT reorganisation_plans_linked_change_id_fkey FOREIGN KEY (linked_change_id) REFERENCES docs.change_proposals(change_id);


--
-- Name: reorganisation_reviews reorganisation_reviews_plan_id_fkey; Type: FK CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.reorganisation_reviews
    ADD CONSTRAINT reorganisation_reviews_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES docs.reorganisation_plans(plan_id) ON DELETE CASCADE;


--
-- Name: reorganisation_reviews reorganisation_reviews_reviewer_principal_id_fkey; Type: FK CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.reorganisation_reviews
    ADD CONSTRAINT reorganisation_reviews_reviewer_principal_id_fkey FOREIGN KEY (reviewer_principal_id) REFERENCES docplane.principals(principal_id);


--
-- Name: trust_mutation_receipts trust_mutation_receipts_principal_id_fkey; Type: FK CONSTRAINT; Schema: docs; Owner: -
--

ALTER TABLE ONLY docs.trust_mutation_receipts
    ADD CONSTRAINT trust_mutation_receipts_principal_id_fkey FOREIGN KEY (principal_id) REFERENCES docplane.principals(principal_id);


--
-- Name: initiative_activities initiative_activities_author_principal_id_fkey; Type: FK CONSTRAINT; Schema: work; Owner: -
--

ALTER TABLE ONLY work.initiative_activities
    ADD CONSTRAINT initiative_activities_author_principal_id_fkey FOREIGN KEY (author_principal_id) REFERENCES docplane.principals(principal_id);


--
-- Name: initiative_activities initiative_activities_initiative_id_fkey; Type: FK CONSTRAINT; Schema: work; Owner: -
--

ALTER TABLE ONLY work.initiative_activities
    ADD CONSTRAINT initiative_activities_initiative_id_fkey FOREIGN KEY (initiative_id) REFERENCES work.initiatives(initiative_id) ON DELETE CASCADE;


--
-- Name: initiative_dependencies initiative_dependencies_depends_on_initiative_id_fkey; Type: FK CONSTRAINT; Schema: work; Owner: -
--

ALTER TABLE ONLY work.initiative_dependencies
    ADD CONSTRAINT initiative_dependencies_depends_on_initiative_id_fkey FOREIGN KEY (depends_on_initiative_id) REFERENCES work.initiatives(initiative_id) ON DELETE CASCADE;


--
-- Name: initiative_dependencies initiative_dependencies_initiative_id_fkey; Type: FK CONSTRAINT; Schema: work; Owner: -
--

ALTER TABLE ONLY work.initiative_dependencies
    ADD CONSTRAINT initiative_dependencies_initiative_id_fkey FOREIGN KEY (initiative_id) REFERENCES work.initiatives(initiative_id) ON DELETE CASCADE;


--
-- Name: initiative_links initiative_links_created_by_fkey; Type: FK CONSTRAINT; Schema: work; Owner: -
--

ALTER TABLE ONLY work.initiative_links
    ADD CONSTRAINT initiative_links_created_by_fkey FOREIGN KEY (created_by) REFERENCES docplane.principals(principal_id);


--
-- Name: initiative_links initiative_links_initiative_id_fkey; Type: FK CONSTRAINT; Schema: work; Owner: -
--

ALTER TABLE ONLY work.initiative_links
    ADD CONSTRAINT initiative_links_initiative_id_fkey FOREIGN KEY (initiative_id) REFERENCES work.initiatives(initiative_id) ON DELETE CASCADE;


--
-- Name: initiatives initiatives_owner_principal_id_fkey; Type: FK CONSTRAINT; Schema: work; Owner: -
--

ALTER TABLE ONLY work.initiatives
    ADD CONSTRAINT initiatives_owner_principal_id_fkey FOREIGN KEY (owner_principal_id) REFERENCES docplane.principals(principal_id);


--
-- Name: initiatives initiatives_promotion_change_id_fkey; Type: FK CONSTRAINT; Schema: work; Owner: -
--

ALTER TABLE ONLY work.initiatives
    ADD CONSTRAINT initiatives_promotion_change_id_fkey FOREIGN KEY (promotion_change_id) REFERENCES docs.change_proposals(change_id);


--
-- Name: initiatives initiatives_workspace_id_fkey; Type: FK CONSTRAINT; Schema: work; Owner: -
--

ALTER TABLE ONLY work.initiatives
    ADD CONSTRAINT initiatives_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES docplane.workspaces(workspace_id);


--
-- Name: mutation_receipts mutation_receipts_principal_id_fkey; Type: FK CONSTRAINT; Schema: work; Owner: -
--

ALTER TABLE ONLY work.mutation_receipts
    ADD CONSTRAINT mutation_receipts_principal_id_fkey FOREIGN KEY (principal_id) REFERENCES docplane.principals(principal_id);


--
-- PostgreSQL database dump complete
--



-- Stable system workspaces. These identifiers are product constants, not install-time randomness.
INSERT INTO docplane.workspaces
    (workspace_id, workspace_key, name, workspace_kind, visibility,
     mutation_policy, default_verification_days, retention_policy,
     system_workspace, created_at, updated_at)
VALUES
    ('d0c00000-0000-5000-8000-000000000001', 'reference',
     'Reference', 'REFERENCE', 'INTERNAL', 'PROPOSAL_REQUIRED', 180,
     '{}'::jsonb, TRUE, now(), now()),
    ('d0c00000-0000-5000-8000-000000000002', 'operations',
     'Operations', 'OPERATIONS', 'INTERNAL', 'PROPOSAL_REQUIRED', 90,
     '{}'::jsonb, TRUE, now(), now()),
    ('d0c00000-0000-5000-8000-000000000003', 'work',
     'Active Work', 'WORK', 'INTERNAL', 'DIRECT_LOW_RISK', NULL,
     '{}'::jsonb, TRUE, now(), now()),
    ('d0c00000-0000-5000-8000-000000000004', 'migration-import',
     'Migration Import', 'MIGRATION', 'INTERNAL', 'PROPOSAL_REQUIRED', NULL,
     '{}'::jsonb, TRUE, now(), now());
