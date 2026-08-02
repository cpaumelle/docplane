-- 011: extend the capture-kind vocabulary for the distraction ledger.
--
-- Mid-session discoveries are dominated by two shapes the original
-- vocabulary made callers shoehorn: a defect noticed while doing something
-- else (BUG — previously filed as FINDING) and a possible enhancement
-- (IMPROVEMENT — previously filed as IDEA). Naming them keeps triage and
-- reporting honest ("how many bugs did we park this week?") without
-- changing any existing row or caller: CHECK extended by adding values
-- only — the compatibility contract's textbook additive case (008 is the
-- exemplar).

ALTER TABLE work.captures DROP CONSTRAINT captures_kind_check;
ALTER TABLE work.captures ADD CONSTRAINT captures_kind_check
    CHECK (kind IN ('IDEA', 'NEXT_ACTION', 'FINDING', 'QUESTION', 'BUG', 'IMPROVEMENT'));
