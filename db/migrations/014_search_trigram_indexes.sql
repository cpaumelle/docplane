-- Token-aware search issues leading-wildcard ILIKE predicates across these
-- four fields. pg_trgm GIN indexes keep that search contract while bounding
-- scan cost as the corpus grows.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX pages_title_trgm_idx
    ON docs.pages USING gin (title gin_trgm_ops);
CREATE INDEX pages_path_trgm_idx
    ON docs.pages USING gin (path gin_trgm_ops);
CREATE INDEX pages_nav_path_trgm_idx
    ON docs.pages USING gin (nav_path gin_trgm_ops);
CREATE INDEX pages_content_trgm_idx
    ON docs.pages USING gin (content gin_trgm_ops);
