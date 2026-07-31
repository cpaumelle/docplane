-- Sprint 8: project authoritative Observe coverage gaps into a bounded,
-- reopenable Work queue and distinguish overlay-derived model wiring.
--
-- Coverage remains derived by observe_api; this table is only the governed
-- triage projection.  A subject/kind pair has one durable identity so a gap
-- can resolve and recur without minting duplicate work.

ALTER TABLE model.entity_links
    ADD COLUMN metadata jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (pg_column_size(metadata) <= 8192);

CREATE TABLE work.coverage_gap_items (
    work_item_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    gap_kind text NOT NULL CHECK (gap_kind IN (
        'MISSING_DESCRIPTION', 'PAGING_ALERT_MISSING_RUNBOOK'
    )),
    subject_entity_id uuid NOT NULL REFERENCES model.entities(entity_id),
    subject_entity_key text NOT NULL,
    page_path text,
    briefing jsonb NOT NULL CHECK (pg_column_size(briefing) <= 8192),
    status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'RESOLVED')),
    created_by uuid NOT NULL REFERENCES docplane.principals(principal_id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_transition_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    version bigint NOT NULL DEFAULT 1 CHECK (version >= 1),
    UNIQUE (gap_kind, subject_entity_id),
    CHECK (
        (status = 'OPEN' AND resolved_at IS NULL)
        OR (status = 'RESOLVED' AND resolved_at IS NOT NULL)
    )
);

CREATE INDEX coverage_gap_items_status_seen_idx
    ON work.coverage_gap_items(status, first_seen_at, gap_kind);
