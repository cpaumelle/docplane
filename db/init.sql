-- Docs stack schema. Runs automatically on first `docker compose up` via
-- /docker-entrypoint-initdb.d. Idempotent — safe to re-run by hand:
--   docker compose exec -T postgres psql -U docs -d docs < db/init.sql
-- Keep in sync with upstream docs-api/init.sql (scripts/sync-from-upstream.sh).

CREATE SCHEMA IF NOT EXISTS docs;

CREATE TABLE IF NOT EXISTS docs.pages (
  id          SERIAL    PRIMARY KEY,
  path        TEXT      UNIQUE NOT NULL,
  title       TEXT      NOT NULL,
  nav_path    TEXT      NOT NULL,
  content     TEXT      NOT NULL,
  revision    TEXT      NOT NULL DEFAULT gen_random_uuid()::text,
  version     INTEGER   NOT NULL DEFAULT 1,
  status      TEXT      NOT NULL DEFAULT 'active',
  created_at  TIMESTAMP NOT NULL DEFAULT now(),
  updated_at  TIMESTAMP NOT NULL DEFAULT now(),
  updated_by  TEXT      NOT NULL DEFAULT 'unknown',

  CONSTRAINT chk_valid_path   CHECK (path ~ '^[a-z0-9/_-]+\.md$'),
  CONSTRAINT chk_content_size CHECK (length(content) < 1000000),
  CONSTRAINT chk_nav_path     CHECK (nav_path = 'Home' OR nav_path ~ '^[^/]+(/[^/]+)+$'),
  CONSTRAINT chk_status       CHECK (status IN ('draft', 'active', 'inactive', 'archived'))
);

CREATE TABLE IF NOT EXISTS docs.page_versions (
  id          SERIAL    PRIMARY KEY,
  path        TEXT      NOT NULL,
  content     TEXT      NOT NULL,
  revision    TEXT      NOT NULL,
  updated_by  TEXT,
  title       TEXT,
  nav_path    TEXT,
  version     INTEGER,
  archived_at TIMESTAMP NOT NULL DEFAULT now()
);

-- Idempotent upgrades for installs created before title/nav_path/version were
-- snapshotted (every write path in main.py inserts all three).
ALTER TABLE docs.page_versions ADD COLUMN IF NOT EXISTS title    TEXT;
ALTER TABLE docs.page_versions ADD COLUMN IF NOT EXISTS nav_path TEXT;
ALTER TABLE docs.page_versions ADD COLUMN IF NOT EXISTS version  INTEGER;

CREATE INDEX IF NOT EXISTS docs_pages_path_idx         ON docs.pages(path);
CREATE INDEX IF NOT EXISTS docs_page_versions_path_idx ON docs.page_versions(path);

-- Top-level section ordering for the rendered nav sidebar.
-- Sections not listed here sort alphabetically after all listed ones.
-- Seeded from application defaults on first startup if empty
-- (see DEFAULT_SECTION_ORDER in docs-api/app/generator.py); manage at
-- runtime via PUT /api/docs/nav/sections.
CREATE TABLE IF NOT EXISTS docs.sections (
  name        TEXT      PRIMARY KEY,
  sort_order  INTEGER   NOT NULL,
  updated_at  TIMESTAMP NOT NULL DEFAULT now(),
  updated_by  TEXT      NOT NULL DEFAULT 'unknown',

  CONSTRAINT chk_section_name CHECK (name = btrim(name) AND name !~ '/' AND name <> 'Home')
);

CREATE INDEX IF NOT EXISTS docs_sections_order_idx ON docs.sections(sort_order);
