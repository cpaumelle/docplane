-- Sprint 10: close the knowledge-class vocabulary after audit/backfill.
--
-- NULL remains deliberately legal: absence is governed by the Observatory
-- audit surface, while this constraint prevents misspelled classifications.

ALTER TABLE docs.pages
    ADD CONSTRAINT pages_knowledge_class_check
    CHECK (
        knowledge_class IS NULL OR knowledge_class IN (
            'ARCHITECTURE', 'OPERATION', 'REFERENCE', 'POLICY',
            'DECISION', 'EVIDENCE', 'DESIGN', 'WORK_NOTE'
        )
    );
