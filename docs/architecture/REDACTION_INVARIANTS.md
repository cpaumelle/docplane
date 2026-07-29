# Redaction / Sanitisation Invariants

When source documents are imported, a redaction transform may rewrite
secret-shaped material into markers. This page states the invariants that
transform must satisfy. They are enforced by an executable regression suite
(`migration/`, `scripts/redaction_ci_scan.py`) and a path-scoped CI workflow
(`redaction-regression`).

All examples on this page are synthetic.

## Marker grammar

A redaction marker is exactly:

    <REDACTED:CLASS:LABEL>

where `CLASS` is `UPPER_SNAKE` and `LABEL` is a synthetic, non-secret
identifier (for example an example page id such as `example-0001`). Anything
that resembles a marker but does not match this grammar exactly is
**malformed** and must be rejected — never produced.

## Invariants

1. **Approved placeholders survive untouched.** Documentation placeholders and
   substitution tokens are never redacted:
   `{{password}}`, `<VAR>`, `$ENV` / `${ENV}`, and the literal `changeme`.

2. **Ordinary identifiers are not rewritten.** Service names and identifiers
   that merely resemble credential words (e.g. `password-service`,
   `token-broker`) are left intact. Redaction keys off token *shape*, not off
   the appearance of a sensitive word.

3. **Braces stay balanced.** Transformed output always has balanced `{`/`}`.
   The historical defect produced `{<REDACTED:PASSWORD:...>}}` (an unbalanced
   marker); such input is now rejected and can never be produced. A generic
   copy of that malformed case lives in `migration/fixtures/malformed-marker.md`
   and the suite asserts it is rejected.

4. **Fenced / executable code is byte-for-byte intact.** Content inside fenced
   code blocks is never rewritten, so runnable examples stay syntactically
   valid.

5. **The transform is deterministic.** Identical input plus identical rules
   yields identical output and an identical sanitised content hash.

6. **A byte-changing rewrite mints a new revision.** At the importer level, if
   redaction changes the bytes of a document, the imported page must carry a
   **newly minted** revision distinct from the source revision. Retaining the
   source revision after a content change (revision-identity retention) is
   forbidden. If redaction changes nothing, the source revision is retained.

7. **Marker counts cannot drift silently.** The number of well-formed markers
   is observable, and an idempotent re-run of the transform must not change it.

8. **Malformed markers are rejected or surfaced.** Malformed `<REDACTED:...>`
   markers cause the transform to raise rather than pass content through.

9. **No invalid Markdown structure is introduced.** Headings and explicit
   anchors are stable unless the source itself changes them.

## Regression layers

- **Unit** — `migration/tests/test_redaction.py` proves the transform's
  invariants (1–5, 7–9).
- **Importer** — `migration/tests/test_importer_revision_identity.py` proves
  the revision-identity invariant (6).
- **CI assertion** — `scripts/redaction_ci_scan.py` scans transformed fixtures
  for unbalanced braces, malformed markers, and marker-count drift.
- **End-to-end** — `migration/tests/test_import_e2e.py` drives a synthetic
  import manifest and proves source revision → transformed content hash →
  newly-minted revision → deterministic replay.
