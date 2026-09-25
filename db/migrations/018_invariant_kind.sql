-- Invariants become first-class model entities: one INVARIANT entity per
-- governed invariant record, imported from per-domain YAML sources in git by
-- scripts/invariant_catalogue.py and rendered as generated catalogue pages.
-- Git holds the authoring history and review trail; the entity carries the
-- current state only. Additive under the compatibility contract, exactly as
-- 008 added MONITOR_RULE: a CHECK vocabulary extended by one value — every
-- previously valid row remains valid, and an older image simply never mints
-- the new kind.

ALTER TABLE model.entities DROP CONSTRAINT entities_entity_kind_check;
ALTER TABLE model.entities ADD CONSTRAINT entities_entity_kind_check
    CHECK (entity_kind IN (
        'SYSTEM', 'SERVICE', 'NODE', 'VM', 'SITE', 'NETWORK', 'DATABASE',
        'SCHEMA', 'API', 'ROUTE', 'DEVICE_MODEL', 'INTERFACE', 'ARTIFACT',
        'MONITOR_RULE', 'INVARIANT'));
