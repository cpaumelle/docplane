-- Sprint 6 (exemplar B): monitoring rules become first-class model entities
-- so the meter list can WATCHES-wire them to services and the soak
-- monitoring reference can resolve against a stable identity instead of
-- free text. Additive under the compatibility contract: a CHECK vocabulary
-- extended by adding a value — every previously valid row remains valid,
-- and an older image simply never mints the new kind.

ALTER TABLE model.entities DROP CONSTRAINT entities_entity_kind_check;
ALTER TABLE model.entities ADD CONSTRAINT entities_entity_kind_check
    CHECK (entity_kind IN (
        'SYSTEM', 'SERVICE', 'NODE', 'VM', 'SITE', 'NETWORK', 'DATABASE',
        'SCHEMA', 'API', 'ROUTE', 'DEVICE_MODEL', 'INTERFACE', 'ARTIFACT',
        'MONITOR_RULE'));
