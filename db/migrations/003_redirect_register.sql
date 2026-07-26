-- WP8 — PostgreSQL is the sole redirect authority.

CREATE TABLE IF NOT EXISTS docs.redirects (
    from_path   text        PRIMARY KEY,
    to_path     text        NOT NULL,
    revision    text        NOT NULL,
    version     integer     NOT NULL DEFAULT 1,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  text        NOT NULL DEFAULT 'api',

    CONSTRAINT redirects_source_differs_from_target CHECK (from_path <> to_path),
    CONSTRAINT redirects_from_path_canonical
        CHECK (from_path ~ '^[a-z0-9/_-]+\.md$'),
    CONSTRAINT redirects_to_path_canonical
        CHECK (to_path ~ '^[a-z0-9/_-]+\.md$'),
    CONSTRAINT redirects_revision_is_uuid
        CHECK (revision ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'),
    CONSTRAINT redirects_version_positive CHECK (version >= 1)
);

CREATE OR REPLACE FUNCTION docs.redirects_monotonic()
RETURNS trigger AS $$
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
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS redirects_monotonic_trg ON docs.redirects;
CREATE TRIGGER redirects_monotonic_trg
    BEFORE UPDATE ON docs.redirects
    FOR EACH ROW EXECUTE FUNCTION docs.redirects_monotonic();

CREATE INDEX IF NOT EXISTS redirects_to_path_idx ON docs.redirects (to_path);
