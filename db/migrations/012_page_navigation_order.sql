-- Explicit, revisioned sibling order for first-class navigation editing.
-- Existing corpora retain their current deterministic alphabetical order until
-- an author reorders a section in the Dashboard.
ALTER TABLE docs.pages
    ADD COLUMN IF NOT EXISTS nav_order integer NOT NULL DEFAULT 1000
    CHECK (nav_order >= 0);

ALTER TABLE docs.page_versions
    ADD COLUMN IF NOT EXISTS nav_order integer NOT NULL DEFAULT 1000
    CHECK (nav_order >= 0);

CREATE INDEX IF NOT EXISTS pages_nav_order_idx
    ON docs.pages(nav_path, nav_order) WHERE status = 'active';
