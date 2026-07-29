# Redaction / Sanitisation Invariants

When source documents are imported, a redaction transform may rewrite
secret-shaped material into markers. This page states the invariants that
transform must satisfy. They are enforced by an executable regression suite
(`migration/`, `scripts/redaction_ci_scan.py`) and a path-scoped CI workflow
(`redaction-regression`).

All examples on this page are synthetic.

## Canonical transform (single entrypoint)

`migration/redaction.py` is the **canonical** sanitisation transform: the single
entrypoint (`migration.redaction.redact`, re-exported as
`migration.parity.canonical_transform`) that every importer MUST call. It is not
one of several interchangeable implementations. See issue #60 for the policy.

Any additional importer code path is a thin **adapter** that delegates to the
canonical entrypoint; it MUST NOT re-implement detection, marker emission, fence
policy, or revision minting. A reusable **parity harness**
(`migration/parity.py`) enforces this: for identical fixtures, any importer path
must produce identical sanitised bytes, identical marker counts, identical
transformed hash, identical revision-minting decision, and identical
malformed / refusal outcome. This makes it structurally impossible for two
transforms to silently diverge.

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

4. **Fenced code is redacted, not blindly preserved (REVISED).** The earlier
   "fenced code preserved byte-for-byte" rule was wrong for production: it let a
   confirmed-secret-shaped value survive purely because it sat inside a
   ```` ``` ```` fence. The revised policy is:

   - Approved placeholders / synthetic examples inside a fence remain unchanged
     (e.g. `{{password}}`, `<VAR>`, `${ENV}` in a ```` ```yaml ```` block).
   - Real / confirmed-secret-shaped values inside a fence **ARE** redacted.
   - The replacement preserves valid shell / YAML / JSON / config syntax
     wherever it can be guaranteed — the bare `<REDACTED:CLASS:LABEL>` marker is
     emitted only in a syntax-safe position (an assignment RHS, a `key: value`
     scalar, or a quoted string), so the surrounding code stays parseable.
   - When a safe syntactic replacement **cannot be guaranteed** (e.g. the token
     is embedded in a URL with no delimiter), the importer **REFUSES** the
     document (`DocumentRefusedError`) and emits a **content-free** remediation
     finding — reason code, marker class, and fence language only. No silent
     pass, and no broken output.
   - No importer may preserve a confirmed credential just because it is inside a
     fence.

   Three synthetic fenced fixtures pin this behaviour:
   `migration/fixtures/fence-approved-placeholder.md` (must remain unchanged),
   `migration/fixtures/fence-secret-safe.md` (must be redacted in place), and
   `migration/fixtures/fence-unsafe-refuse.md` (must fail closed with a
   content-free finding).

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

10. **Unsafe redaction fails closed.** When the transform cannot guarantee a
    safe result (an unsafe fenced replacement per invariant 4), it refuses the
    document rather than emitting partial, broken, or secret-bearing output, and
    surfaces a content-free remediation finding. Fail-closed, never fail-open.

11. **Two transforms can never silently diverge (parity).** The canonical
    transform is the single entrypoint; any second importer path must yield an
    identical parity fingerprint — identical sanitised bytes, marker count,
    transformed hash, revision-minting decision, and malformed / refusal
    outcome. The parity harness (`migration/parity.py`) and
    `migration/tests/test_redaction_parity.py` enforce this and prove a
    divergent re-implementation is caught.

## Regression layers

- **Unit** — `migration/tests/test_redaction.py` proves the transform's
  invariants (1–5, 7–10), including the revised fenced-code policy (redact in
  safe positions, preserve approved placeholders, refuse when unsafe).
- **Parity** — `migration/tests/test_redaction_parity.py` proves invariant 11:
  the five parity properties are stable on the canonical path, a delegating
  adapter matches canonical, and a divergent re-implementation is caught.
- **Importer** — `migration/tests/test_importer_revision_identity.py` proves
  the revision-identity invariant (6).
- **CI assertion** — `scripts/redaction_ci_scan.py` scans transformed fixtures
  for unbalanced braces, malformed markers, and marker-count drift, and asserts
  the malformed fixture is rejected and the unsafe-fence fixture is refused.
- **End-to-end** — `migration/tests/test_import_e2e.py` drives a synthetic
  import manifest and proves source revision → transformed content hash →
  newly-minted revision → deterministic replay.
