-- PATCH_TEXT is revision-bound audited intent; publication still stores a
-- complete immutable page version before committing the constructed result.
ALTER TABLE docs.change_operations
    DROP CONSTRAINT IF EXISTS change_operations_operation_type_check;
ALTER TABLE docs.change_operations
    ADD CONSTRAINT change_operations_operation_type_check CHECK (operation_type IN (
        'CREATE_PAGE', 'REPLACE_DOCUMENT', 'PATCH_TEXT', 'PATCH_METADATA',
        'REPLACE_SECTION', 'INSERT_BEFORE_HEADING', 'INSERT_AFTER_HEADING',
        'MOVE_PAGE', 'REPARENT_NAV', 'ARCHIVE_PAGE', 'RESTORE_PAGE',
        'ADD_REDIRECT', 'REMOVE_REDIRECT', 'REORDER_SECTIONS'
    ));
